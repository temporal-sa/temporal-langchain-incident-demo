"""Build the console's view of a run from Temporal itself.

The graph is never a scripted animation. Each poll combines:

1. the workflow's event history (what happened, with decoded inputs and results),
2. DescribeWorkflowExecution (what is in flight right now, including the current
   retry attempt and last failure of a pending activity, and whether the workflow
   task itself is failing), and
3. the ``get_state`` query (the agent's conversation and phase), which only works
   when a worker is up. If it fails, the console says so, which is exactly what you
   want to show during the worker-crash scenario.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from temporalio.client import Client, WorkflowQueryFailedError
from temporalio.service import RPCError

from incident_agent.api.annotate import annotate
from incident_agent.config import Settings
from incident_agent.shared.constants import CHANGE_TASK_QUEUE

ACTIVITY_LANES = {
    "call_llm": "llm",
    "discover_mcp_tools": "mcp",
    "call_mcp_tool": "mcp",
    "open_ticket": "tools",
    "add_ticket_note": "tools",
    "update_ticket": "tools",
    "notify_oncall": "tools",
    "check_service_health": "tools",
}

IN_FLIGHT = ("scheduled", "running", "retrying")

# Java activity type names (method names, capitalized) -> what the console shows.
JAVA_ACTIVITY_LABELS = {
    "ValidateChange": "Validate change against the CMDB",
    "OpenChangeRecord": "Open change record",
    "ExecuteChange": "Execute the change",
    "CloseChangeRecord": "Close change record",
}


class SnapshotBuilder:
    def __init__(self, client: Client, settings: Settings) -> None:
        self.client = client
        self.settings = settings
        self._closed_children: dict[str, list[dict[str, Any]]] = {}

    # ------------------------------------------------------------------ #

    async def build(self, workflow_id: str) -> dict[str, Any]:
        handle = self.client.get_workflow_handle(workflow_id)
        desc = await handle.describe()
        raw = desc.raw_description

        events = [e async for e in handle.fetch_history_events()]
        steps = await self._steps(events)
        workflow_task = self._merge_pending(raw, steps)
        annotate(steps, default_chat_model=self.settings.chat_model_class)
        await self._attach_child_steps(steps)
        state, worker = await self._query_state(handle)

        status = desc.status.name if desc.status else "UNKNOWN"
        ns = self.settings.temporal_namespace
        ui = self.settings.temporal_ui_url
        return {
            "workflow_id": desc.id,
            "run_id": desc.run_id,
            "workflow_type": desc.workflow_type,
            "status": status,
            "start_time": _ms(desc.start_time),
            "close_time": _ms(desc.close_time),
            "search_attributes": _search_attributes(desc),
            "steps": steps,
            "workflow_task": workflow_task,
            "active_lanes": sorted({s["lane"] for s in steps if s["status"] in IN_FLIGHT}),
            "state": state,
            "worker": worker,
            "links": {
                "temporal_ui": f"{ui}/namespaces/{ns}/workflows/{desc.id}/{desc.run_id}/history",
            },
        }

    # ------------------------------------------------------------------ #
    # History -> steps                                                   #
    # ------------------------------------------------------------------ #

    async def _steps(self, events: list[Any]) -> list[dict[str, Any]]:
        steps: list[dict[str, Any]] = []
        by_event: dict[int, dict[str, Any]] = {}

        for e in events:
            kind = e.WhichOneof("attributes")
            ts = e.event_time.ToMilliseconds()
            eid = e.event_id

            if kind == "workflow_execution_started_event_attributes":
                a = e.workflow_execution_started_event_attributes
                inp = await self._first(a.input)
                steps.append(_step(eid, "workflow", "workflow", "start", f"{a.workflow_type.name} started",
                                   "completed", ts, ended=ts, input=inp,
                                   detail=_brief_dict(inp, ["service", "scenario", "llm_mode"])))

            elif kind == "activity_task_scheduled_event_attributes":
                a = e.activity_task_scheduled_event_attributes
                name = a.activity_type.name
                inp = await self._first(a.input)
                s = _step(eid, ACTIVITY_LANES.get(name, "tools"), "activity", name,
                          _activity_label(name, inp, None, self.settings.llm_label), "scheduled", ts,
                          input=_compact_input(name, inp), detail=_activity_detail(name, inp, None))
                s["_raw_input"] = inp
                s["activity_id"] = a.activity_id
                s["max_attempts"] = a.retry_policy.maximum_attempts if a.HasField("retry_policy") else 0
                by_event[eid] = s
                steps.append(s)

            elif kind == "activity_task_started_event_attributes":
                a = e.activity_task_started_event_attributes
                s = by_event.get(a.scheduled_event_id)
                if s:
                    s["attempt"] = a.attempt
                    s["status"] = "running"

            elif kind == "activity_task_completed_event_attributes":
                a = e.activity_task_completed_event_attributes
                s = by_event.get(a.scheduled_event_id)
                if s:
                    out = await self._first(a.result)
                    s.update(status="completed", ended_at=ts, output=out,
                             label=_activity_label(s["name"], s.get("_raw_input"), out, self.settings.llm_label),
                             detail=_activity_detail(s["name"], s.get("_raw_input"), out))
                    if s["name"] == "call_mcp_tool" and isinstance(out, dict) and out.get("is_error"):
                        s["status"] = "completed_with_error"

            elif kind == "activity_task_failed_event_attributes":
                a = e.activity_task_failed_event_attributes
                s = by_event.get(a.scheduled_event_id)
                if s:
                    s.update(status="failed", ended_at=ts, error=_failure_text(a.failure))

            elif kind == "activity_task_timed_out_event_attributes":
                a = e.activity_task_timed_out_event_attributes
                s = by_event.get(a.scheduled_event_id)
                if s:
                    s.update(status="failed", ended_at=ts, error="Timed out")

            elif kind == "activity_task_canceled_event_attributes":
                a = e.activity_task_canceled_event_attributes
                s = by_event.get(a.scheduled_event_id)
                if s:
                    s.update(status="cancelled", ended_at=ts)

            elif kind == "timer_started_event_attributes":
                a = e.timer_started_event_attributes
                secs = a.start_to_fire_timeout.ToTimedelta().total_seconds()
                s = _step(eid, "workflow", "timer", "timer", f"Durable timer {_fmt_secs(secs)}", "running", ts,
                          detail="Waiting without holding a worker thread")
                s["timer_seconds"] = secs
                by_event[eid] = s
                steps.append(s)

            elif kind == "timer_fired_event_attributes":
                s = by_event.get(e.timer_fired_event_attributes.started_event_id)
                if s:
                    s.update(status="completed", ended_at=ts)

            elif kind == "timer_canceled_event_attributes":
                s = by_event.get(e.timer_canceled_event_attributes.started_event_id)
                if s:
                    s.update(status="cancelled", ended_at=ts, detail="Condition met before the deadline")

            elif kind == "start_child_workflow_execution_initiated_event_attributes":
                a = e.start_child_workflow_execution_initiated_event_attributes
                inp = await self._first(a.input)
                is_java = a.task_queue.name == CHANGE_TASK_QUEUE
                action = (inp or {}).get("action", "") if isinstance(inp, dict) else ""
                s = _step(eid, "java" if is_java else "workflow", "child", a.workflow_type.name,
                          f"{a.workflow_type.name}: {action}".rstrip(": "), "scheduled", ts, input=inp,
                          detail=f"{'Java worker' if is_java else 'Worker'} on task queue {a.task_queue.name}")
                s["child_workflow_id"] = a.workflow_id
                by_event[eid] = s
                steps.append(s)

            elif kind == "child_workflow_execution_started_event_attributes":
                a = e.child_workflow_execution_started_event_attributes
                s = by_event.get(a.initiated_event_id)
                if s:
                    s["status"] = "running"
                    s["child_run_id"] = a.workflow_execution.run_id

            elif kind == "child_workflow_execution_completed_event_attributes":
                a = e.child_workflow_execution_completed_event_attributes
                s = by_event.get(a.initiated_event_id)
                if s:
                    out = await self._first(a.result)
                    change_id = out.get("change_id") if isinstance(out, dict) else None
                    s.update(status="completed", ended_at=ts, output=out,
                             detail=f"{change_id or 'change'} completed by the Java worker")

            elif kind in (
                "child_workflow_execution_failed_event_attributes",
                "child_workflow_execution_timed_out_event_attributes",
                "child_workflow_execution_terminated_event_attributes",
                "child_workflow_execution_canceled_event_attributes",
                "start_child_workflow_execution_failed_event_attributes",
            ):
                a = getattr(e, kind)
                s = by_event.get(getattr(a, "initiated_event_id", 0))
                if s:
                    error = _failure_text(a.failure) if hasattr(a, "failure") and a.HasField("failure") else kind
                    s.update(status="failed", ended_at=ts, error=error)

            elif kind == "workflow_execution_signaled_event_attributes":
                a = e.workflow_execution_signaled_event_attributes
                arg = await self._first(a.input)
                label = {"send_message": "Follow-up question", "close_incident": "Close session"}.get(
                    a.signal_name, f"Signal {a.signal_name}")
                steps.append(_step(eid, "human", "signal", a.signal_name, label, "completed", ts, ended=ts,
                                   input=arg, detail=_clip(str(arg), 140) if arg else ""))

            elif kind == "workflow_execution_update_accepted_event_attributes":
                a = e.workflow_execution_update_accepted_event_attributes
                name = a.accepted_request.input.name
                arg = await self._first(a.accepted_request.input.args)
                s = _step(eid, "human", "update", name, _update_label(name, arg), "running", ts, input=arg,
                          detail=_clip(str((arg or {}).get("comment", "")), 140) if isinstance(arg, dict) else "")
                by_event[eid] = s
                steps.append(s)

            elif kind == "workflow_execution_update_completed_event_attributes":
                a = e.workflow_execution_update_completed_event_attributes
                s = by_event.get(a.accepted_event_id)
                if s:
                    which = a.outcome.WhichOneof("value")
                    if which == "success":
                        s.update(status="completed", ended_at=ts, output=await self._first(a.outcome.success))
                    else:
                        s.update(status="failed", ended_at=ts, error=_failure_text(a.outcome.failure))

            elif kind == "upsert_workflow_search_attributes_event_attributes":
                a = e.upsert_workflow_search_attributes_event_attributes
                decoded: dict[str, Any] = {}
                for key, payload in a.search_attributes.indexed_fields.items():
                    decoded[key] = await self._one(payload)
                steps.append(_step(eid, "workflow", "search_attributes", "upsert", "Search attributes",
                                   "completed", ts, ended=ts, output=decoded,
                                   detail=", ".join(f"{k}={v}" for k, v in decoded.items())))

            elif kind == "workflow_task_failed_event_attributes":
                a = e.workflow_task_failed_event_attributes
                steps.append(_step(eid, "workflow", "workflow_task", "workflow_task", "Workflow task failed",
                                   "failed", ts, ended=ts, error=_failure_text(a.failure),
                                   detail="Temporal keeps retrying the task. Fix the code and restart the worker."))

            elif kind == "workflow_execution_completed_event_attributes":
                out = await self._first(e.workflow_execution_completed_event_attributes.result)
                steps.append(_step(eid, "workflow", "workflow", "complete", "Workflow completed", "completed",
                                   ts, ended=ts, output=out,
                                   detail=_brief_dict(out, ["outcome", "change_id", "total_tokens"])))

            elif kind == "workflow_execution_failed_event_attributes":
                a = e.workflow_execution_failed_event_attributes
                steps.append(_step(eid, "workflow", "workflow", "fail", "Workflow failed", "failed", ts, ended=ts,
                                   error=_failure_text(a.failure)))

            elif kind == "workflow_execution_terminated_event_attributes":
                a = e.workflow_execution_terminated_event_attributes
                steps.append(_step(eid, "workflow", "workflow", "terminate", "Workflow terminated", "failed",
                                   ts, ended=ts, error=a.reason or "terminated"))

            elif kind == "workflow_execution_continued_as_new_event_attributes":
                steps.append(_step(eid, "workflow", "workflow", "continue_as_new", "Continued as new",
                                   "completed", ts, ended=ts,
                                   detail="History compacted; state carried into a fresh run"))

        for s in steps:
            s.pop("_raw_input", None)
        return steps

    async def _attach_child_steps(self, steps: list[dict[str, Any]]) -> None:
        """Fetch each child workflow's own history (the Java side) and attach its
        activities, so the console can show the same workflow-activity-service
        pattern inside the Java workflow."""
        for s in steps:
            child_id = s.get("child_workflow_id")
            if not child_id:
                continue
            if child_id in self._closed_children:
                s["child_steps"] = self._closed_children[child_id]
                continue
            if not s.get("child_run_id"):
                s["child_steps"] = []
                continue
            try:
                handle = self.client.get_workflow_handle(child_id, run_id=s["child_run_id"])
                events = [e async for e in handle.fetch_history_events()]
            except Exception:  # noqa: BLE001 - the parent view still works without it
                s["child_steps"] = []
                continue
            child_steps = await self._child_activity_steps(events)
            s["child_steps"] = child_steps
            if s.get("status") in ("completed", "failed"):
                self._closed_children[child_id] = child_steps

    async def _child_activity_steps(self, events: list[Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        by_event: dict[int, dict[str, Any]] = {}
        for e in events:
            kind = e.WhichOneof("attributes")
            ts = e.event_time.ToMilliseconds()
            if kind == "activity_task_scheduled_event_attributes":
                a = e.activity_task_scheduled_event_attributes
                sub = {
                    "name": a.activity_type.name,
                    "label": JAVA_ACTIVITY_LABELS.get(a.activity_type.name, a.activity_type.name),
                    "status": "scheduled",
                    "attempt": 1,
                    "error": None,
                    "detail": "",
                    "started_at": ts,
                    "ended_at": None,
                }
                by_event[e.event_id] = sub
                out.append(sub)
            elif kind == "activity_task_started_event_attributes":
                a = e.activity_task_started_event_attributes
                sub = by_event.get(a.scheduled_event_id)
                if sub:
                    sub["attempt"] = a.attempt
                    sub["status"] = "running"
            elif kind == "activity_task_completed_event_attributes":
                a = e.activity_task_completed_event_attributes
                sub = by_event.get(a.scheduled_event_id)
                if sub:
                    result = await self._first(a.result)
                    sub.update(status="completed", ended_at=ts)
                    if isinstance(result, list):
                        sub["detail"] = "; ".join(str(r) for r in result)
                    elif isinstance(result, str):
                        sub["detail"] = result
            elif kind == "activity_task_failed_event_attributes":
                a = e.activity_task_failed_event_attributes
                sub = by_event.get(a.scheduled_event_id)
                if sub:
                    sub.update(status="failed", ended_at=ts, error=_failure_text(a.failure))
        return out

    def _merge_pending(self, raw: Any, steps: list[dict[str, Any]]) -> dict[str, Any]:
        open_steps = {s["activity_id"]: s for s in steps if s.get("activity_id") and s["status"] in IN_FLIGHT}
        for pa in raw.pending_activities:
            s = open_steps.get(pa.activity_id)
            if not s:
                continue
            last_failure = pa.last_failure.message if pa.HasField("last_failure") else None
            s["attempt"] = pa.attempt
            if pa.maximum_attempts:
                s["max_attempts"] = pa.maximum_attempts
            if pa.attempt > 1 and last_failure:
                s["status"] = "retrying"
                s["error"] = last_failure
            elif _pending_state(pa) == "STARTED":
                s["status"] = "running"

        info = {"attempt": 0, "failing": False}
        if raw.HasField("pending_workflow_task"):
            attempt = raw.pending_workflow_task.attempt
            info = {"attempt": attempt, "failing": attempt > 1}
        return info

    async def _query_state(self, handle: Any) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
        try:
            state = await handle.query("get_state", rpc_timeout=timedelta(seconds=2.5))
            return state, {"responding": True, "detail": "Worker answered the state query."}
        except WorkflowQueryFailedError as e:
            return None, {"responding": True, "detail": f"Worker is up but could not replay this workflow: {_clip(str(e), 200)}"}
        except RPCError:
            return None, {
                "responding": False,
                "detail": "No worker answered. State is safe in Temporal and resumes when a worker returns.",
            }
        except Exception as e:  # noqa: BLE001 - surface anything else to the console
            return None, {"responding": False, "detail": _clip(str(e), 200)}

    async def _first(self, payloads: Any) -> Any:
        items = list(getattr(payloads, "payloads", []) or [])
        if not items:
            return None
        try:
            values = await self.client.data_converter.decode(items)
            return values[0] if values else None
        except Exception:  # noqa: BLE001
            return "<undecodable payload>"

    async def _one(self, payload: Any) -> Any:
        try:
            values = await self.client.data_converter.decode([payload])
            return values[0] if values else None
        except Exception:  # noqa: BLE001
            return "<undecodable>"


# ---------------------------------------------------------------------- #
# Run list                                                               #
# ---------------------------------------------------------------------- #


async def list_runs(client: Client, limit: int = 25) -> list[dict[str, Any]]:
    query = 'WorkflowType = "IncidentWorkflow" OR WorkflowType = "TriageWorkflow"'
    runs: list[dict[str, Any]] = []
    async for wf in client.list_workflows(query):
        status = wf.status.name if wf.status else "UNKNOWN"
        if status == "CONTINUED_AS_NEW":
            continue
        runs.append({
            "workflow_id": wf.id,
            "run_id": wf.run_id,
            "workflow_type": wf.workflow_type,
            "status": status,
            "start_time": _ms(wf.start_time),
            "close_time": _ms(wf.close_time),
            "search_attributes": _search_attributes(wf),
        })
        if len(runs) >= limit:
            break
    runs.sort(key=lambda r: r["start_time"] or 0, reverse=True)
    return runs


# ---------------------------------------------------------------------- #
# Formatting helpers                                                     #
# ---------------------------------------------------------------------- #


def _step(eid: int, lane: str, kind: str, name: str, label: str, status: str, ts: int, *,
          ended: Optional[int] = None, input: Any = None, output: Any = None,
          detail: str = "", error: Optional[str] = None) -> dict[str, Any]:
    return {
        "id": f"e{eid}", "event_id": eid, "lane": lane, "kind": kind, "name": name, "label": label,
        "detail": detail, "status": status, "attempt": 1, "max_attempts": 0, "error": error,
        "started_at": ts, "ended_at": ended, "input": input, "output": output, "_raw_input": input,
    }


def _activity_label(name: str, inp: Any, out: Any, llm_label: str = "Claude") -> str:
    i = inp if isinstance(inp, dict) else {}
    o = out if isinstance(out, dict) else {}
    if name == "call_llm":
        who = "Scripted model" if i.get("llm_mode") == "mock" else llm_label
        if out is None:
            return f"{who} is deciding"
        names = [tc.get("name") for tc in o.get("tool_calls") or [] if isinstance(tc, dict)]
        return f"{who} chose {', '.join(names)}" if names else f"{who} replied"
    if name == "discover_mcp_tools":
        return f"Discovered {len(o.get('tools') or [])} MCP tools" if out is not None else "Discover MCP tools"
    if name == "call_mcp_tool":
        return f"MCP {i.get('tool', 'tool')}"
    if name == "open_ticket":
        return f"Opened {o.get('ticket_id')}" if out is not None else "Open ticket"
    if name == "add_ticket_note":
        return "Ticket note"
    if name == "update_ticket":
        return f"Ticket set to {i.get('status') or 'updated'}"
    if name == "notify_oncall":
        return "Page on-call"
    if name == "check_service_health":
        return f"Health check {i.get('check_number', '?')}/{i.get('total_checks', '?')}"
    return name


def _activity_detail(name: str, inp: Any, out: Any) -> str:
    i = inp if isinstance(inp, dict) else {}
    o = out if isinstance(out, dict) else {}
    if name == "call_llm":
        base = f"{i.get('purpose', '')} turn {i.get('turn', '?')}, step {int(i.get('iteration', 0)) + 1}"
        if out is not None:
            base += f"; {o.get('input_tokens', 0)} in, {o.get('output_tokens', 0)} out; {o.get('model', '')}"
        return base
    if name == "call_mcp_tool":
        args = i.get("args") or {}
        brief = ", ".join(f"{k}={v}" for k, v in args.items())
        if out is not None:
            prefix = "tool error: " if o.get("is_error") else ""
            return _clip(f"{prefix}{o.get('content', '')}", 160)
        return _clip(brief, 160)
    if name in ("add_ticket_note", "update_ticket"):
        return _clip(str(i.get("note", "")), 160)
    if name == "notify_oncall":
        return _clip(str(i.get("message", "")), 160)
    if name == "check_service_health" and out is not None:
        return str(o.get("summary", ""))
    if isinstance(out, str):
        return _clip(out, 160)
    return ""


def _compact_input(name: str, inp: Any) -> Any:
    """Keep snapshots small. The full payload is always in the Temporal UI."""
    if name == "call_llm" and isinstance(inp, dict):
        messages = inp.get("messages") or []
        last = messages[-1] if messages else {}
        return {
            "purpose": inp.get("purpose"),
            "llm_mode": inp.get("llm_mode"),
            "turn": inp.get("turn"),
            "iteration": inp.get("iteration"),
            "message_count": len(messages),
            "last_message": {k: last.get(k) for k in ("role", "tool_name", "content")} if isinstance(last, dict) else None,
            "tools": [t.get("name") for t in inp.get("tools") or [] if isinstance(t, dict)],
            "note": "Full conversation and tool schemas are in the Temporal UI event history.",
        }
    return inp


def _update_label(name: str, arg: Any) -> str:
    if name == "submit_approval" and isinstance(arg, dict):
        verdict = "Approved" if arg.get("approved") else "Rejected"
        return f"{verdict} by {arg.get('reviewer', 'reviewer')}"
    return f"Update {name}"


def _failure_text(failure: Any) -> str:
    parts: list[str] = []
    current = failure
    depth = 0
    while current is not None and depth < 6:
        if current.message:
            parts.append(current.message)
        current = current.cause if current.HasField("cause") else None
        depth += 1
    return " / caused by: ".join(parts) or "failed"


def _search_attributes(obj: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    typed = getattr(obj, "typed_search_attributes", None)
    for pair in getattr(typed, "search_attributes", []) or []:
        value = pair.value
        out[pair.key.name] = value.isoformat() if isinstance(value, datetime) else value
    return out


def _pending_state(pa: Any) -> str:
    try:
        from temporalio.api.enums.v1 import PendingActivityState

        return PendingActivityState.Name(pa.state).replace("PENDING_ACTIVITY_STATE_", "")
    except Exception:  # noqa: BLE001
        return str(pa.state)


def _brief_dict(value: Any, keys: list[str]) -> str:
    if not isinstance(value, dict):
        return ""
    return ", ".join(f"{k}={value[k]}" for k in keys if value.get(k) not in (None, ""))


def _fmt_secs(secs: float) -> str:
    if secs >= 3600:
        return f"{secs / 3600:.1f}h"
    if secs >= 60:
        return f"{secs / 60:.0f}m"
    return f"{secs:.0f}s"


def _clip(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "\u2026"


def _ms(dt: Optional[datetime]) -> Optional[int]:
    return int(dt.timestamp() * 1000) if dt else None
