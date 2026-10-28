"""Demo scenarios. Each preset picks a service whose MCP data steers the agent
toward a particular path, plus any fault to inject."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    blurb: str
    service: str
    severity: str
    alert: str
    talking_point: str
    shows: list[str] = field(default_factory=list)
    monitor_checks: int = 3
    monitor_interval_seconds: int = 4
    approval_timeout_seconds: int = 900
    idle_timeout_seconds: int = 1800
    fault: Optional[dict[str, Any]] = None

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("fault", None)
        return data


SCENARIOS: dict[str, Scenario] = {
    s.key: s
    for s in [
        Scenario(
            key="happy_path",
            title="Happy path",
            blurb="Memory leak on checkout-api. The agent investigates over MCP, restarts automatically, and verifies.",
            service="checkout-api",
            severity="SEV3",
            alert="p95 latency above 2s for 10 minutes; memory climbing",
            talking_point="Every LLM decision and every tool call is its own activity in history.",
            shows=["MCP tools", "Activity tools", "Java child workflow", "Durable timers"],
        ),
        Scenario(
            key="human_in_loop",
            title="Human approval",
            blurb="Bad deploy on payments-api. The agent proposes a rollback, which waits for a person.",
            service="payments-api",
            severity="SEV1",
            alert="5xx error rate at 18% since the last deploy",
            talking_point="The workflow can wait hours for approval without holding a thread.",
            shows=["Validated Update", "Approval timeout", "Search attributes"],
        ),
        Scenario(
            key="api_downtime",
            title="API downtime",
            blurb="The metrics backend behind the MCP server fails four times, then recovers on attempt five.",
            service="inventory-api",
            severity="SEV2",
            alert="DB connection pool exhausted; intermittent 503s",
            talking_point="Retries are configured once, in the workflow, not inside LangChain.",
            shows=["Activity retry policy", "MCP outage"],
            fault={"service": "inventory-api", "tool": "get_metrics", "fail_times": 4},
        ),
        Scenario(
            key="workflow_bug",
            title="Bug in workflow code",
            blurb="A deliberate bug in the change-policy code. Fix the line, restart the worker, watch it resume.",
            service="search-api",
            severity="SEV2",
            alert="Thread pool saturated; request queue growing",
            talking_point="Completed LLM calls are not repeated after the fix. The context survives the bug.",
            shows=["Workflow task retry", "Replay"],
        ),
        Scenario(
            key="unrecoverable",
            title="Unrecoverable failure",
            blurb="legacy-billing is not under automated change management. Java rejects the change; the workflow compensates and fails.",
            service="legacy-billing",
            severity="SEV2",
            alert="Invoice batch job heap exhaustion",
            talking_point="A non-retryable error from Java fails the Python workflow, with compensation first.",
            shows=["Non-retryable error", "Cross-language failure", "Compensation"],
        ),
        Scenario(
            key="long_running",
            title="Long-running session",
            blurb="Slow leak on orders-api with a two-minute verification window. Kill the worker mid-run.",
            service="orders-api",
            severity="SEV2",
            alert="Gradual memory growth; GC pauses lengthening",
            talking_point="Kill the worker during verification. Restart it. Ask a follow-up; the agent remembers.",
            shows=["Worker crash recovery", "Durable timers", "Follow-up chat"],
            monitor_checks=12,
            monitor_interval_seconds=10,
            idle_timeout_seconds=7200,
        ),
    ]
}

TRIAGE_SERVICES = ["payments-api", "checkout-api", "inventory-api", "orders-api", "search-api"]

TRIAGE_ALERTS = {
    "payments-api": "5xx error rate at 18% since the last deploy",
    "checkout-api": "p95 latency above 2s; memory climbing",
    "inventory-api": "Intermittent 503s",
    "orders-api": "Gradual memory growth",
    "search-api": "Request queue growing",
}

VISIBILITY_QUERIES = [
    {"label": "Waiting for approval", "query": 'WorkflowType = "IncidentWorkflow" AND AgentPhase = "AwaitingApproval"'},
    {"label": "Failed incidents", "query": 'WorkflowType = "IncidentWorkflow" AND ExecutionStatus = "Failed"'},
    {"label": "SEV1 incidents", "query": 'IncidentSeverity = "SEV1"'},
    {"label": "Rollbacks", "query": 'RemediationAction = "rollback"'},
    {"label": "Heavy token use", "query": "LlmTokens > 6000"},
    {"label": "Quick triages", "query": 'WorkflowType = "TriageWorkflow"'},
]
