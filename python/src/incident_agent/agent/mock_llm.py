"""A scripted stand-in for Claude.

Select it per run ("Scripted" in the console, or llm_mode="mock"). It honors the
same request/response contract as the real call_llm activity and follows the
runbook using the actual MCP tool results, so everything except the model is
real: Temporal, MCP, the Java worker, approvals, retries.

Use it to rehearse offline, run tests, or keep a demo going if the model API is
unreachable.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from incident_agent.shared.models import ChatMessage, LlmRequest, LlmResponse, ToolCall

MODEL_NAME = "scripted-mock"


def respond(req: LlmRequest) -> LlmResponse:
    turn = _current_turn(req.messages)
    step = sum(1 for m in turn if m.role == "assistant")
    results = {m.tool_name: m for m in turn if m.role == "tool" and m.tool_name}
    available = {t.name for t in req.tools}
    service = str(req.context.get("service", "the service"))

    if req.purpose == "triage":
        text, calls = _triage(req, service, step, results, available)
    elif req.purpose == "investigate":
        text, calls = _investigate(req, service, step, results, available)
    else:
        text, calls = _follow_up(req, service, results)

    return LlmResponse(
        content=text,
        tool_calls=calls,
        model=MODEL_NAME,
        input_tokens=_estimate_tokens(req),
        output_tokens=max(12, len(text) // 4 + 25 * len(calls)),
        stop_reason="tool_use" if calls else "end_turn",
    )


# --------------------------------------------------------------------------- #
# Scripts                                                                      #
# --------------------------------------------------------------------------- #


def _investigate(req, service, step, results, available):
    if step == 0:
        return (
            "Pulling the service record, current metrics, and recent error logs.",
            _calls(req, step, available, [
                ("get_service_info", {"service": service}),
                ("get_metrics", {"service": service, "window_minutes": 30}),
                ("search_logs", {"service": service, "query": "error", "window_minutes": 30}),
            ]),
        )

    info = results.get("get_service_info")
    if info is not None and info.is_error:
        return (
            f"## Impact\nUnknown.\n\n## Root cause\n{service} is not in the service catalog, "
            "so I can't investigate or change it safely.\n\n## Action taken\nNone.\n\n"
            "## Verification\nNot applicable.\n\n## Follow-ups\nConfirm the alert source and the service name.",
            [],
        )

    if step == 1:
        finding = _first_error_line(results.get("search_logs")) or "error spike in logs"
        return (
            "Checking recent deploys and recording the main finding.",
            _calls(req, step, available, [
                ("get_recent_deploys", {"service": service, "limit": 5}),
                ("add_ticket_note", {"note": f"Key finding: {finding}"}),
            ]),
        )

    if step == 2:
        action, target, rationale = _decide(results)
        args: dict[str, Any] = {"action": action, "rationale": rationale}
        if target:
            args["target_version"] = target
        return (
            f"Proposing a {action}.",
            _calls(req, step, available, [("propose_remediation", args)]),
        )

    outcome = results.get("propose_remediation")
    return _summary(service, results, outcome), []


def _triage(req, service, step, results, available):
    if step == 0:
        return (
            "Quick look at metrics and logs.",
            _calls(req, step, available, [
                ("get_service_info", {"service": service}),
                ("get_metrics", {"service": service, "window_minutes": 15}),
                ("search_logs", {"service": service, "query": "error", "window_minutes": 15}),
            ]),
        )
    if step == 1 and "get_recent_deploys" in available:
        return (
            "Checking deploy history.",
            _calls(req, step, available, [("get_recent_deploys", {"service": service, "limit": 3})]),
        )
    action, _, rationale = _decide(results)
    metrics = _json(results.get("get_metrics")) or {}
    error_rate = float(metrics.get("error_rate_pct", 0) or 0)
    severity = "SEV1" if error_rate >= 10 else "SEV2" if error_rate >= 2 else "SEV3"
    return (
        f"Severity: {severity}\nLikely cause: {rationale}\nRecommended next step: {action} {service} "
        "through the incident workflow.",
        [],
    )


def _follow_up(req, service, results):
    proposal_args: Optional[dict[str, Any]] = None
    proposal_result: Optional[str] = None
    for m in req.messages:
        for tc in m.tool_calls:
            if tc.name == "propose_remediation":
                proposal_args = tc.args
        if m.role == "tool" and m.tool_name == "propose_remediation":
            proposal_result = m.content
    question = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
    if not proposal_args:
        return (f"No remediation has been proposed yet for {service}. You asked: \u201c{question}\u201d.", [])
    action = proposal_args.get("action", "a change")
    return (
        f"From this session\u2019s record: I proposed a {action} because {proposal_args.get('rationale', 'of the evidence gathered')}. "
        f"Outcome: {proposal_result or 'pending'}",
        [],
    )


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _decide(results: dict[str, ChatMessage]) -> tuple[str, Optional[str], str]:
    deploys = (_json(results.get("get_recent_deploys")) or {}).get("deploys") or []
    recent = [d for d in deploys if float(d.get("minutes_ago", 1e9)) <= 60]
    if recent and len(deploys) >= 2:
        bad, good = deploys[0], deploys[1]
        return (
            "rollback",
            str(good.get("version")),
            f"Errors began right after deploy {bad.get('version')} ({bad.get('minutes_ago')} minutes ago); "
            f"{good.get('version')} was healthy.",
        )
    metrics = _json(results.get("get_metrics")) or {}
    trend = metrics.get("trend") or "resource exhaustion with no recent deploy"
    return "restart", None, f"{trend}; no deploy in the last hour, so a rolling restart is the safe fix."


def _summary(service: str, results: dict[str, ChatMessage], outcome: Optional[ChatMessage]) -> str:
    metrics = _json(results.get("get_metrics")) or {}
    finding = _first_error_line(results.get("search_logs")) or "see ticket notes"
    result_text = outcome.content if outcome else "No remediation result."
    rejected = "Rejected" in result_text or "No approval" in result_text
    return (
        f"## Impact\n{service}: error rate {metrics.get('error_rate_pct', '?')}%, "
        f"p95 {metrics.get('p95_latency_ms', '?')} ms.\n\n"
        f"## Root cause\n{metrics.get('trend', 'Under investigation')}. Log evidence: {finding}\n\n"
        f"## Action taken\n{result_text}\n\n"
        f"## Verification\n{'Not run; change not executed.' if rejected else 'Health checks recorded above.'}\n\n"
        "## Follow-ups\n"
        + ("Reviewer to choose the next step; incident escalated." if rejected
           else "Add an alert on the leading indicator and a regression test for the fault.")
    )


def _calls(req: LlmRequest, step: int, available: set[str], wanted: list[tuple[str, dict]]) -> list[ToolCall]:
    return [
        ToolCall(id=f"toolu_mock_{req.turn}_{step}_{i}", name=name, args=args)
        for i, (name, args) in enumerate(wanted)
        if name in available
    ]


def _current_turn(messages: list[ChatMessage]) -> list[ChatMessage]:
    last_user = max((i for i, m in enumerate(messages) if m.role == "user"), default=0)
    return messages[last_user:]


def _json(message: Optional[ChatMessage]) -> Optional[dict[str, Any]]:
    if message is None or message.is_error:
        return None
    try:
        value = json.loads(message.content)
        return value if isinstance(value, dict) else None
    except (TypeError, ValueError):
        return None


def _first_error_line(message: Optional[ChatMessage]) -> Optional[str]:
    data = _json(message) or {}
    for line in data.get("lines") or []:
        if "ERROR" in str(line) or "FATAL" in str(line):
            return str(line)
    lines = data.get("lines") or []
    return str(lines[0]) if lines else None


def _estimate_tokens(req: LlmRequest) -> int:
    chars = len(req.system_prompt) + sum(len(m.content) for m in req.messages)
    chars += sum(len(t.description) + 120 for t in req.tools)
    return max(50, chars // 4)
