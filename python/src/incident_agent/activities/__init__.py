"""Activity implementations. Imported by the worker only, never by workflow code."""

from incident_agent.activities.llm import call_llm
from incident_agent.activities.mcp_tools import call_mcp_tool, discover_mcp_tools
from incident_agent.activities.ops_tools import (
    add_ticket_note,
    check_service_health,
    notify_oncall,
    open_ticket,
    update_ticket,
)

ALL_ACTIVITIES = [
    call_llm,
    discover_mcp_tools,
    call_mcp_tool,
    open_ticket,
    add_ticket_note,
    update_ticket,
    notify_oncall,
    check_service_health,
]
