"""Unit tests for the scripted LLM (no Temporal server needed)."""

import json

from incident_agent.agent import mock_llm
from incident_agent.shared.models import ChatMessage, LlmRequest, ToolSpec

TOOLS = [
    ToolSpec(name=n, description=n, input_schema={"type": "object", "properties": {}}, source=src)
    for n, src in [
        ("get_service_info", "mcp"), ("get_metrics", "mcp"), ("search_logs", "mcp"),
        ("get_recent_deploys", "mcp"), ("add_ticket_note", "activity"), ("propose_remediation", "workflow"),
    ]
]


def _req(messages, purpose="investigate"):
    return LlmRequest(purpose=purpose, llm_mode="mock", system_prompt="sys", messages=messages,
                      tools=TOOLS, context={"service": "payments-api"}, turn=1)


def _tool(name, payload, error=False):
    return ChatMessage(role="tool", tool_name=name, tool_call_id=f"id-{name}",
                       content=payload if isinstance(payload, str) else json.dumps(payload), is_error=error)


def test_first_step_gathers_evidence_in_parallel():
    resp = mock_llm.respond(_req([ChatMessage(role="user", content="ALERT")]))
    assert [c.name for c in resp.tool_calls] == ["get_service_info", "get_metrics", "search_logs"]


def test_recent_deploy_leads_to_rollback():
    msgs = [
        ChatMessage(role="user", content="ALERT"),
        ChatMessage(role="assistant", content="a"),
        _tool("get_service_info", {"service": "payments-api"}),
        _tool("get_metrics", {"error_rate_pct": 18}),
        _tool("search_logs", {"lines": ["ERROR NullPointerException"]}),
        ChatMessage(role="assistant", content="b"),
        _tool("get_recent_deploys", {"deploys": [
            {"version": "2.14.0", "minutes_ago": 22}, {"version": "2.13.4", "minutes_ago": 4000}]}),
    ]
    resp = mock_llm.respond(_req(msgs))
    assert resp.tool_calls[0].name == "propose_remediation"
    assert resp.tool_calls[0].args["action"] == "rollback"
    assert resp.tool_calls[0].args["target_version"] == "2.13.4"


def test_unknown_service_stops_without_remediation():
    msgs = [
        ChatMessage(role="user", content="ALERT"),
        ChatMessage(role="assistant", content="a"),
        _tool("get_service_info", "Service 'x' not found", error=True),
    ]
    resp = mock_llm.respond(_req(msgs))
    assert resp.tool_calls == []
    assert "not in the service catalog" in resp.content
