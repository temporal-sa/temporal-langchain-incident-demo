"""Tools we own (as opposed to tools discovered from the MCP server).

Two kinds:

* source="activity": the model calls it, the workflow runs it as a Temporal
  activity (durable, retried, recorded in history).
* source="workflow": handled inside workflow code because it orchestrates other
  durable primitives (policy, human approval, a cross-language child workflow,
  timers). A single call may legitimately take hours.
"""

from incident_agent.shared.constants import Activities
from incident_agent.shared.models import ToolSpec

PROPOSE_REMEDIATION = ToolSpec(
    name="propose_remediation",
    description=(
        "Propose exactly one remediation for the incident. The workflow applies change "
        "policy: low-risk actions (restart) run automatically; high-risk actions "
        "(rollback, scale_out) pause for human approval. Approved changes are executed "
        "by the change-management system and verified with health checks before this "
        "tool returns, so the result tells you what actually happened."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["restart", "rollback", "scale_out"],
                "description": "restart for leaks or resource exhaustion; rollback when errors began right after a deploy; scale_out for pure load.",
            },
            "rationale": {
                "type": "string",
                "description": "One or two sentences citing the evidence.",
            },
            "target_version": {
                "type": "string",
                "description": "Required for rollback: the last known-good version.",
            },
        },
        "required": ["action", "rationale"],
    },
    source="workflow",
)

NOTIFY_ONCALL = ToolSpec(
    name="notify_oncall",
    description="Page the on-call engineer for the service with a short status message.",
    input_schema={
        "type": "object",
        "properties": {
            "message": {"type": "string"},
            "urgency": {"type": "string", "enum": ["low", "high"]},
        },
        "required": ["message"],
    },
    source="activity",
)

ADD_TICKET_NOTE = ToolSpec(
    name="add_ticket_note",
    description="Append a finding to the incident ticket. Use it to record key evidence.",
    input_schema={
        "type": "object",
        "properties": {"note": {"type": "string"}},
        "required": ["note"],
    },
    source="activity",
)

NATIVE_TOOLS: list[ToolSpec] = [ADD_TICKET_NOTE, NOTIFY_ONCALL, PROPOSE_REMEDIATION]

# Model-facing tool name -> activity type that implements it.
ACTIVITY_TOOL_ROUTES: dict[str, str] = {
    NOTIFY_ONCALL.name: Activities.NOTIFY_ONCALL,
    ADD_TICKET_NOTE.name: Activities.ADD_TICKET_NOTE,
}

HIGH_RISK_ACTIONS = {"rollback", "scale_out"}
VALID_ACTIONS = {"restart", "rollback", "scale_out"}
