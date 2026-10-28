"""Add loop structure to history-derived steps.

Event history is flat. This pass recovers the structure the console draws:

* ``primitive``: what Temporal construct the step is (workflow, activity,
  child_workflow, timer, update, signal, visibility, workflow_task).
* ``service``: the standard service an activity talks to (llm, mcp,
  ticketing, paging, monitoring).
* ``segment``: "outer" (IncidentWorkflow orchestration), "inner" (an agent turn:
  a Claude decision or a tool the model asked for), or "wftool" (workflow code
  running the propose_remediation tool: policy, approval, Java change, timers).
* ``turn`` / ``iteration`` / ``tool_call_id`` / ``role`` for inner-loop steps.

It needs no workflow changes: a ``call_llm`` result lists the tool calls Claude
chose, and the activities that follow are matched to them by tool name and
arguments. Anything else that runs before the next decision belongs to the
workflow-resident tool, if Claude chose one.

Pure functions with no Temporal imports, so they are easy to unit test.
"""

from __future__ import annotations

from typing import Any, Optional

PRIMITIVE_BY_KIND = {
    "activity": "activity",
    "child": "child_workflow",
    "timer": "timer",
    "update": "update",
    "signal": "signal",
    "search_attributes": "visibility",
    "workflow_task": "workflow_task",
    "workflow": "workflow",
}

SERVICE_BY_ACTIVITY = {
    "call_llm": "llm",
    "discover_mcp_tools": "mcp",
    "call_mcp_tool": "mcp",
    "open_ticket": "ticketing",
    "add_ticket_note": "ticketing",
    "update_ticket": "ticketing",
    "notify_oncall": "paging",
    "check_service_health": "monitoring",
}

WORKFLOW_TOOLS = {"propose_remediation"}


def annotate(steps: list[dict[str, Any]], default_chat_model: str = "ChatAnthropic") -> list[dict[str, Any]]:
    ctx: Optional[dict[str, Any]] = None
    phase: Optional[str] = None

    for s in steps:
        s["primitive"] = PRIMITIVE_BY_KIND.get(s.get("kind", ""), s.get("kind"))
        s["service"] = SERVICE_BY_ACTIVITY.get(s.get("name", "")) if s.get("kind") == "activity" else None
        s.setdefault("segment", "outer")
        s.setdefault("role", "outer")
        s.setdefault("turn", None)
        s.setdefault("iteration", None)
        s.setdefault("tool_call_id", None)
        s.setdefault("tool_name", None)

        if s.get("kind") == "timer":
            _label_timer(s, phase)

        if s.get("kind") == "search_attributes":
            new_phase = (s.get("output") or {}).get("AgentPhase") if isinstance(s.get("output"), dict) else None
            if new_phase:
                phase = new_phase

        if s.get("kind") in ("workflow", "signal"):
            # Workflow start, completion, failure, and queued follow-up messages
            # belong to the outer loop even if they land mid-turn.
            continue

        if s.get("name") == "call_llm":
            inp = s.get("input") if isinstance(s.get("input"), dict) else {}
            out = s.get("output") if isinstance(s.get("output"), dict) else None
            calls = [c for c in (out or {}).get("tool_calls") or [] if isinstance(c, dict)]
            s.update(segment="inner", role="decide", turn=inp.get("turn"), iteration=inp.get("iteration"))
            s["chosen_tools"] = [c.get("name") for c in calls]
            s["langchain"] = _langchain_call(inp, (out or {}).get("chat_model") or default_chat_model)
            if out is not None and not calls:
                ctx = None  # Claude replied with text: the turn is over.
            else:
                wf_call = next((c for c in calls if c.get("name") in WORKFLOW_TOOLS), None)
                ctx = {
                    "turn": inp.get("turn"),
                    "iteration": inp.get("iteration"),
                    "pending": [c for c in calls if c.get("name") not in WORKFLOW_TOOLS],
                    "wftool": wf_call,
                }
            continue

        if ctx is None:
            continue

        base = {"turn": ctx["turn"], "iteration": ctx["iteration"]}
        matched = _match(s, ctx["pending"])
        if matched is not None:
            s.update(segment="inner", role="tool", tool_call_id=matched.get("id"), tool_name=matched.get("name"), **base)
        elif ctx["wftool"] is not None:
            s.update(segment="wftool", role="wftool", tool_call_id=ctx["wftool"].get("id"),
                     tool_name=ctx["wftool"].get("name"), **base)
        else:
            s.update(segment="inner", role="other", **base)

    return steps


def _match(step: dict[str, Any], pending: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if step.get("kind") != "activity" or not pending:
        return None
    name = step.get("name")
    inp = step.get("input") if isinstance(step.get("input"), dict) else {}

    def same(call: dict[str, Any], exact: bool) -> bool:
        cname = call.get("name")
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        if name == "call_mcp_tool":
            return inp.get("tool") == cname and (not exact or (inp.get("args") or {}) == args)
        if name == "add_ticket_note":
            return cname == "add_ticket_note" and (not exact or inp.get("note") == str(args.get("note", "")))
        if name == "notify_oncall":
            return cname == "notify_oncall" and (not exact or inp.get("message") == str(args.get("message", "")))
        return False

    for exact in (True, False):
        for i, call in enumerate(pending):
            if same(call, exact):
                return pending.pop(i)
    return None


def _langchain_call(inp: dict[str, Any], chat_model: str) -> str:
    if inp.get("llm_mode") == "mock":
        return "Scripted model (LangChain and the LLM bypassed)"
    tools = len(inp.get("tools") or [])
    messages = inp.get("message_count") or 0
    return f"{chat_model}.bind_tools({tools} tools).ainvoke({messages + 1} messages)"


def _label_timer(step: dict[str, Any], phase: Optional[str]) -> None:
    secs = float(step.get("timer_seconds") or 0)
    dur = fmt_secs(secs)
    if phase == "AwaitingApproval":
        step["label"] = f"Approval deadline, {dur}"
        step["timer_purpose"] = "approval"
        step["detail"] = ("Cleared: the decision arrived first" if step.get("status") == "cancelled"
                          else "Durable wait for a person. No thread is held.")
    elif phase == "Verifying":
        step["label"] = f"Wait {dur} before a health check"
        step["timer_purpose"] = "verification"
        step["detail"] = "Durable timer. Survives worker restarts."
    elif phase in ("Resolved", "Escalated"):
        step["label"] = f"Session idle timeout, {dur}"
        step["timer_purpose"] = "idle"
        step["detail"] = ("Cleared: a follow-up or close arrived" if step.get("status") == "cancelled"
                          else "The session stays open for follow-up questions. No thread is held.")
    else:
        step["timer_purpose"] = "wait"
        if step.get("status") == "cancelled":
            step["detail"] = "Cleared: the condition was met first"


def fmt_secs(secs: float) -> str:
    if secs >= 3600:
        return f"{secs / 3600:.1f}h"
    if secs >= 60:
        return f"{secs / 60:.0f}m"
    return f"{secs:.0f}s"
