"""Data contracts.

Everything here is a plain dataclass serialized by Temporal's default JSON payload
converter. Keeping these framework-free means:

* workflow code can import them inside the determinism sandbox,
* the event history is readable JSON in the Temporal UI, and
* the Java worker can deserialize ChangeRequest / ChangeResult with Jackson
  (snake_case field names are the cross-language contract).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# --------------------------------------------------------------------------- #
# Conversation + tools (framework-neutral; LangChain types live in activities) #
# --------------------------------------------------------------------------- #


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatMessage:
    """One entry of the durable conversation the workflow owns.

    role: "user" | "assistant" | "tool"
    """

    role: str
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None
    is_error: bool = False
    turn: int = 0
    author: Optional[str] = None


@dataclass
class ToolSpec:
    """A tool the model may call.

    source: "mcp" (discovered from the MCP server), "activity" (a Temporal activity
    we own), or "workflow" (handled in workflow code, e.g. the approval-gated
    remediation tool).
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    source: str


@dataclass
class LlmRequest:
    purpose: str
    llm_mode: str
    system_prompt: str
    messages: list[ChatMessage]
    tools: list[ToolSpec]
    context: dict[str, Any] = field(default_factory=dict)
    turn: int = 0
    iteration: int = 0


@dataclass
class LlmResponse:
    content: str
    tool_calls: list[ToolCall]
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = ""
    chat_model: str = ""  # LangChain class that made the call, e.g. ChatAnthropic


# --------------------------------------------------------------------------- #
# MCP                                                                          #
# --------------------------------------------------------------------------- #


@dataclass
class McpDiscoveryRequest:
    incident_id: str
    server: str = "ops"


@dataclass
class McpToolCatalog:
    server_url: str
    tools: list[ToolSpec]


@dataclass
class McpToolRequest:
    tool: str
    args: dict[str, Any]
    incident_id: str


@dataclass
class McpToolResult:
    tool: str
    content: str
    is_error: bool = False


# --------------------------------------------------------------------------- #
# Side-effecting tools we own (Temporal activities)                            #
# --------------------------------------------------------------------------- #


@dataclass
class TicketRequest:
    incident_id: str
    service: str
    severity: str
    title: str


@dataclass
class Ticket:
    ticket_id: str
    url: str


@dataclass
class TicketNote:
    ticket_id: str
    note: str
    status: Optional[str] = None


@dataclass
class OncallPage:
    incident_id: str
    service: str
    message: str
    urgency: str = "high"


@dataclass
class HealthCheckRequest:
    incident_id: str
    service: str
    action: str
    check_number: int
    total_checks: int


@dataclass
class HealthCheckResult:
    healthy: bool
    error_rate_pct: float
    p95_latency_ms: int
    summary: str


# --------------------------------------------------------------------------- #
# Remediation, approval, and the Java change-management contract               #
# --------------------------------------------------------------------------- #


@dataclass
class RemediationProposal:
    action: str
    rationale: str
    target_version: Optional[str] = None
    risk: str = "low"
    requires_approval: bool = False
    proposed_at_turn: int = 0


@dataclass
class ApprovalDecision:
    approved: bool
    reviewer: str = "on-call"
    comment: str = ""


@dataclass
class ChangeRequest:
    """Input to the Java ChangeRequestWorkflow. Field names are the JSON contract."""

    incident_id: str
    service: str
    action: str
    target_version: Optional[str]
    rationale: str
    requested_by: str
    approved_by: str


@dataclass
class ChangeResult:
    """Output of the Java ChangeRequestWorkflow."""

    change_id: str
    status: str
    steps: list[str] = field(default_factory=list)
    message: str = ""


# --------------------------------------------------------------------------- #
# Workflow state, inputs, outputs                                              #
# --------------------------------------------------------------------------- #


@dataclass
class SessionState:
    """Everything the agent knows. Lives in workflow memory, rebuilt from event
    history on replay, and carried across continue-as-new."""

    incident_id: str
    service: str
    severity: str
    scenario: str
    llm_mode: str
    phase: str = "Starting"
    ticket_id: Optional[str] = None
    tools: list[ToolSpec] = field(default_factory=list)
    messages: list[ChatMessage] = field(default_factory=list)
    proposal: Optional[RemediationProposal] = None
    approval_status: str = "NotRequired"
    approval: Optional[ApprovalDecision] = None
    awaiting_approval: bool = False
    change_results: list[ChangeResult] = field(default_factory=list)
    health_checks: list[HealthCheckResult] = field(default_factory=list)
    summary: str = ""
    turn: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    agent_busy: bool = False
    continued_runs: int = 0
    error: Optional[str] = None


@dataclass
class IncidentInput:
    incident_id: str
    service: str
    alert: str
    severity: str = "SEV2"
    scenario: str = "happy_path"
    llm_mode: str = "live"  # "live" (configured LLM provider) or "mock" (Scripted)
    monitor_checks: int = 3
    monitor_interval_seconds: int = 4
    approval_timeout_seconds: int = 900
    idle_timeout_seconds: int = 1800
    carried_state: Optional[SessionState] = None


@dataclass
class IncidentReport:
    incident_id: str
    service: str
    outcome: str
    ticket_id: Optional[str]
    summary: str
    remediation: Optional[str]
    change_id: Optional[str]
    llm_calls: int
    tool_calls: int
    total_tokens: int


@dataclass
class TriageInput:
    incident_id: str
    service: str
    alert: str
    severity: str = "SEV3"
    scenario: str = "triage"
    llm_mode: str = "live"


@dataclass
class TriageReport:
    incident_id: str
    service: str
    summary: str
    llm_calls: int
    tool_calls: int
    total_tokens: int
