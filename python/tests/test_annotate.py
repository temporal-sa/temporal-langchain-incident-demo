"""The annotation pass recovers inner/outer loop structure from flat history."""

from incident_agent.api.annotate import annotate


def _act(name, inp=None, out=None, status="completed"):
    return {"kind": "activity", "name": name, "input": inp or {}, "output": out, "status": status}


def _llm(turn, it, calls, mode="anthropic"):
    inp = {"turn": turn, "iteration": it, "llm_mode": mode, "tools": ["a", "b"], "message_count": 3}
    out = {"tool_calls": [{"id": f"t{turn}{it}{i}", "name": n, "args": a} for i, (n, a) in enumerate(calls)]}
    return _act("call_llm", inp, out)


def test_structure_of_a_turn_with_a_workflow_tool():
    steps = [
        {"kind": "workflow", "name": "start", "status": "completed"},
        _act("open_ticket"),
        _act("discover_mcp_tools"),
        _llm(1, 0, [("get_metrics", {"service": "x"}), ("search_logs", {"service": "x"})]),
        _act("call_mcp_tool", {"tool": "get_metrics", "args": {"service": "x"}}),
        _act("call_mcp_tool", {"tool": "search_logs", "args": {"service": "x"}}),
        _llm(1, 1, [("propose_remediation", {"action": "rollback"})]),
        {"kind": "search_attributes", "name": "upsert", "status": "completed", "output": {"AgentPhase": "AwaitingApproval"}},
        _act("notify_oncall", {"message": "Approval needed"}),
        {"kind": "timer", "name": "timer", "status": "cancelled", "timer_seconds": 900, "label": ""},
        {"kind": "update", "name": "submit_approval", "status": "completed"},
        {"kind": "child", "name": "ChangeRequestWorkflow", "status": "completed"},
        {"kind": "search_attributes", "name": "upsert", "status": "completed", "output": {"AgentPhase": "Verifying"}},
        {"kind": "timer", "name": "timer", "status": "completed", "timer_seconds": 4, "label": ""},
        _act("check_service_health"),
        _llm(1, 2, []),
        _act("update_ticket", {"status": "resolved"}),
    ]
    annotate(steps)
    seg = [(s["name"], s["segment"], s["role"]) for s in steps]

    assert seg[1] == ("open_ticket", "outer", "outer")
    assert seg[3] == ("call_llm", "inner", "decide")
    assert seg[4][1:] == ("inner", "tool") and steps[4]["tool_call_id"] == "t100"
    assert seg[5][1:] == ("inner", "tool") and steps[5]["tool_call_id"] == "t101"
    for i in range(7, 15):
        assert steps[i]["segment"] == "wftool", steps[i]
        assert steps[i]["tool_name"] == "propose_remediation"
    assert steps[9]["label"] == "Approval deadline, 15m"
    assert steps[13]["label"] == "Wait 4s before a health check"
    assert seg[15] == ("call_llm", "inner", "decide")
    assert seg[16] == ("update_ticket", "outer", "outer")
    assert steps[4]["service"] == "mcp" and steps[3]["service"] == "llm"
    assert steps[11]["primitive"] == "child_workflow" and steps[10]["primitive"] == "update"
    assert "ChatAnthropic.bind_tools(2 tools)" in steps[3]["langchain"]


def test_activity_tool_matched_by_arguments_and_scripted_mode_labelled():
    steps = [
        _llm(2, 0, [("notify_oncall", {"message": "hi"}), ("add_ticket_note", {"note": "n"})], mode="mock"),
        _act("notify_oncall", {"message": "hi"}),
        _act("add_ticket_note", {"note": "n"}),
    ]
    annotate(steps)
    assert [s["role"] for s in steps] == ["decide", "tool", "tool"]
    assert steps[0]["langchain"].startswith("Scripted model")
