"""Workflow tests against a local Temporal dev server (downloaded on first run).

Activities are replaced with fakes, the LLM runs in scripted mode, and a Python
stub implements the Java ChangeRequestWorkflow contract on the change-management
task queue. That last part doubles as a cross-language contract test: if the
payload shapes drift, these tests break.
"""

import asyncio
import json
import uuid

import pytest
from temporalio import activity, workflow
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from incident_agent.agent import mock_llm
from incident_agent.shared import search_attributes as sa
from incident_agent.shared.constants import AGENT_TASK_QUEUE, CHANGE_TASK_QUEUE, Activities
from incident_agent.shared.models import (
    ApprovalDecision,
    ChangeRequest,
    ChangeResult,
    HealthCheckRequest,
    HealthCheckResult,
    IncidentInput,
    LlmRequest,
    LlmResponse,
    McpDiscoveryRequest,
    McpToolCatalog,
    McpToolRequest,
    McpToolResult,
    OncallPage,
    Ticket,
    TicketNote,
    TicketRequest,
    ToolSpec,
)
from incident_agent.workflows import IncidentWorkflow

DEPLOYS = {
    "payments-api": [{"version": "2.14.0", "minutes_ago": 22}, {"version": "2.13.4", "minutes_ago": 4000}],
}


@activity.defn(name=Activities.CALL_LLM)
async def fake_llm(req: LlmRequest) -> LlmResponse:
    return mock_llm.respond(req)


@activity.defn(name=Activities.DISCOVER_MCP_TOOLS)
async def fake_discover(req: McpDiscoveryRequest) -> McpToolCatalog:
    names = ["get_service_info", "get_metrics", "search_logs", "get_recent_deploys"]
    return McpToolCatalog(server_url="test", tools=[
        ToolSpec(name=n, description=n, input_schema={"type": "object", "properties": {}}, source="mcp") for n in names
    ])


@activity.defn(name=Activities.CALL_MCP_TOOL)
async def fake_mcp(req: McpToolRequest) -> McpToolResult:
    service = req.args.get("service", "")
    data = {
        "get_service_info": {"service": service},
        "get_metrics": {"error_rate_pct": 5, "p95_latency_ms": 900, "trend": "memory climbing"},
        "search_logs": {"lines": ["ERROR something broke"]},
        "get_recent_deploys": {"deploys": DEPLOYS.get(service, [{"version": "1.0.0", "minutes_ago": 9000}])},
    }[req.tool]
    return McpToolResult(tool=req.tool, content=json.dumps(data))


@activity.defn(name=Activities.OPEN_TICKET)
async def fake_open_ticket(req: TicketRequest) -> Ticket:
    return Ticket(ticket_id="TKT-TEST", url="https://example.test/TKT-TEST")


@activity.defn(name=Activities.ADD_TICKET_NOTE)
async def fake_note(note: TicketNote) -> str:
    return "noted"


@activity.defn(name=Activities.UPDATE_TICKET)
async def fake_update(note: TicketNote) -> str:
    return "updated"


@activity.defn(name=Activities.NOTIFY_ONCALL)
async def fake_page(page: OncallPage) -> str:
    return "paged"


@activity.defn(name=Activities.CHECK_SERVICE_HEALTH)
async def fake_health(req: HealthCheckRequest) -> HealthCheckResult:
    healthy = req.check_number == req.total_checks
    return HealthCheckResult(healthy=healthy, error_rate_pct=0.2, p95_latency_ms=200, summary="ok")


@workflow.defn(name="ChangeRequestWorkflow")
class StubJavaChangeWorkflow:
    """Stands in for the Java implementation; same name, same task queue, same JSON."""

    @workflow.run
    async def run(self, req: ChangeRequest) -> ChangeResult:
        if req.service == "legacy-billing":
            raise ApplicationError("Service 'legacy-billing' is not registered", type="ServiceNotChangeManaged", non_retryable=True)
        return ChangeResult(change_id="CHG-TEST", status="COMPLETED", steps=[req.action], message="ok")


ACTIVITIES = [fake_llm, fake_discover, fake_mcp, fake_open_ticket, fake_note, fake_update, fake_page, fake_health]
SEARCH_ATTRIBUTES = [
    sa.INCIDENT_SERVICE, sa.INCIDENT_SEVERITY, sa.DEMO_SCENARIO, sa.AGENT_PHASE,
    sa.APPROVAL_STATUS, sa.REMEDIATION_ACTION, sa.LLM_TOKENS,
]


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_local(search_attributes=SEARCH_ATTRIBUTES) as e:
        yield e


def _input(service: str, scenario: str) -> IncidentInput:
    return IncidentInput(
        incident_id=f"test-{service}-{uuid.uuid4().hex[:6]}",
        service=service,
        alert="test alert",
        scenario=scenario,
        llm_mode="mock",
        monitor_checks=2,
        monitor_interval_seconds=1,
        idle_timeout_seconds=1,
    )


async def _run_workers(env):
    return (
        Worker(env.client, task_queue=AGENT_TASK_QUEUE, workflows=[IncidentWorkflow], activities=ACTIVITIES),
        Worker(env.client, task_queue=CHANGE_TASK_QUEUE, workflows=[StubJavaChangeWorkflow]),
    )


async def test_happy_path_restarts_and_resolves(env):
    agent_worker, java_worker = await _run_workers(env)
    async with agent_worker, java_worker:
        inp = _input("checkout-api", "happy_path")
        report = await env.client.execute_workflow(IncidentWorkflow.run, inp, id=inp.incident_id, task_queue=AGENT_TASK_QUEUE)
    assert report.outcome == "Resolved"
    assert report.remediation == "restart"
    assert report.change_id == "CHG-TEST"


async def test_rollback_waits_for_approval(env):
    agent_worker, java_worker = await _run_workers(env)
    async with agent_worker, java_worker:
        inp = _input("payments-api", "human_in_loop")
        handle = await env.client.start_workflow(IncidentWorkflow.run, inp, id=inp.incident_id, task_queue=AGENT_TASK_QUEUE)
        for _ in range(100):
            state = await handle.query(IncidentWorkflow.get_state)
            if state.awaiting_approval:
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("workflow never asked for approval")
        await handle.execute_update(IncidentWorkflow.submit_approval, ApprovalDecision(approved=True, reviewer="alice"))
        report = await handle.result()
    assert report.remediation == "rollback"
    assert report.outcome == "Resolved"


async def test_java_rejection_fails_workflow_after_compensation(env):
    agent_worker, java_worker = await _run_workers(env)
    async with agent_worker, java_worker:
        inp = _input("legacy-billing", "unrecoverable")
        with pytest.raises(WorkflowFailureError) as info:
            await env.client.execute_workflow(IncidentWorkflow.run, inp, id=inp.incident_id, task_queue=AGENT_TASK_QUEUE)
    cause = info.value.cause
    assert isinstance(cause, ApplicationError)
    assert cause.type == "RemediationRejected"
    assert "not registered" in cause.message
