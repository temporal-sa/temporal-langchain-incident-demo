"""Names that every process agrees on.

These are plain constants (no environment lookups) because workflow code imports
them, and workflow code must stay deterministic.
"""

# Task queue polled by the Python worker (agent workflows + LangChain activities).
AGENT_TASK_QUEUE = "incident-agent"

# Task queue polled by the Java / Spring Boot worker.
CHANGE_TASK_QUEUE = "change-management"

# Workflow type implemented in Java (com.demo.changemgmt.workflow.ChangeRequestWorkflow).
# Python starts it by name; Temporal routes it by task queue. Neither side imports the other.
CHANGE_WORKFLOW_TYPE = "ChangeRequestWorkflow"


class Activities:
    """Activity type names. Workflows call activities by name, so workflow code never
    imports LangChain, the Anthropic SDK, or the MCP client."""

    CALL_LLM = "call_llm"
    DISCOVER_MCP_TOOLS = "discover_mcp_tools"
    CALL_MCP_TOOL = "call_mcp_tool"
    OPEN_TICKET = "open_ticket"
    ADD_TICKET_NOTE = "add_ticket_note"
    UPDATE_TICKET = "update_ticket"
    NOTIFY_ONCALL = "notify_oncall"
    CHECK_SERVICE_HEALTH = "check_service_health"


class Phases:
    STARTING = "Starting"
    INVESTIGATING = "Investigating"
    AWAITING_APPROVAL = "AwaitingApproval"
    REMEDIATING = "Remediating"
    VERIFYING = "Verifying"
    RESOLVED = "Resolved"
    ESCALATED = "Escalated"
    FAILED = "Failed"
    TRIAGING = "Triaging"
    TRIAGED = "Triaged"


class ApprovalStatuses:
    NOT_REQUIRED = "NotRequired"
    AUTO_APPROVED = "AutoApproved"
    PENDING = "Pending"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    TIMED_OUT = "TimedOut"
