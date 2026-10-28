"""Demo console API.

    uv run incident-api          # http://127.0.0.1:8000, proxied by the Vite UI

Everything here is a thin Temporal client: start workflows, send Updates and
Signals, and read history. No agent logic lives in the API.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from temporalio.client import WorkflowUpdateFailedError
from temporalio.common import SearchAttributePair, TypedSearchAttributes
from temporalio.service import RPCError

from incident_agent.api.scenarios import (
    SCENARIOS,
    TRIAGE_ALERTS,
    TRIAGE_SERVICES,
    VISIBILITY_QUERIES,
)
from incident_agent.api.snapshot import SnapshotBuilder, list_runs
from incident_agent.config import settings
from incident_agent.shared import search_attributes as sa
from incident_agent.shared.constants import AGENT_TASK_QUEUE, ApprovalStatuses, Phases
from incident_agent.shared.models import ApprovalDecision, IncidentInput, TriageInput
from incident_agent.temporal_client import connect
from incident_agent.workflows import IncidentWorkflow, TriageWorkflow

log = logging.getLogger("incident_agent.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = await connect()
    app.state.client = client
    app.state.snapshots = SnapshotBuilder(client, settings)
    log.info("Connected to Temporal at %s (namespace %s)", settings.temporal_address, settings.temporal_namespace)
    yield


app = FastAPI(title="Incident copilot demo API", lifespan=lifespan)

# CORS is off by default, and isn't needed when the console reaches this API through its
# same-origin proxy (the normal setup). It's only for serving the console from another
# origin; then list that origin, e.g. CORS_ORIGINS=https://console.example.com
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["content-type"],
    )


class StartIncidentBody(BaseModel):
    scenario: str
    llm_mode: Optional[str] = None


class StartTriageBody(BaseModel):
    service: str = "payments-api"
    llm_mode: Optional[str] = None


class ApprovalBody(BaseModel):
    approved: bool
    reviewer: str = "on-call"
    comment: str = ""


class MessageBody(BaseModel):
    text: str


def _client():
    return app.state.client


def _mode(requested: Optional[str]) -> str:
    mode = (requested or settings.default_llm_mode or "live").lower()
    return "mock" if mode in ("mock", "scripted") else "live"


# ---------------------------------------------------------------------- #


@app.get("/api/config")
async def config() -> dict[str, Any]:
    ns = settings.temporal_namespace
    ui = settings.temporal_ui_url
    return {
        "scenarios": [s.public() for s in SCENARIOS.values()],
        "triage_services": TRIAGE_SERVICES,
        "visibility_queries": VISIBILITY_QUERIES,
        "temporal_ui_url": ui,
        "temporal_workflows_url": f"{ui}/namespaces/{ns}/workflows",
        "namespace": ns,
        "default_llm_mode": _mode(None),
        "llm": _llm_info(),
        "mcp_url": settings.mcp_url,
    }


def _llm_info() -> dict[str, Any]:
    missing = settings.llm_missing_env()
    ready = bool(settings.llm_model) and not missing
    if missing:
        detail = f"{', '.join(missing)} not set"
    elif not settings.llm_model:
        detail = "LLM_MODEL not set"
    else:
        detail = f"{settings.llm_model} through LangChain {settings.chat_model_class}"
    service_sub = {
        "anthropic": "Anthropic API",
        "ollama": "Local model server",
        "bedrock_converse": "AWS Bedrock",
    }.get(settings.llm_provider, f"{settings.llm_label} API")
    if settings.llm_base_url:
        service_sub = f"API at {settings.llm_base_url}"
    return {
        "provider": settings.llm_provider,
        "label": settings.llm_label,
        "model": settings.llm_model,
        "chat_model": settings.chat_model_class,
        "service_sub": service_sub,
        "ready": ready,
        "detail": detail,
    }


@app.post("/api/incidents")
async def start_incident(body: StartIncidentBody) -> dict[str, Any]:
    scenario = SCENARIOS.get(body.scenario)
    if scenario is None:
        raise HTTPException(404, f"Unknown scenario '{body.scenario}'")

    fault_armed = None
    if scenario.fault:
        fault_armed = await _arm_fault(scenario.fault)

    incident_id = f"inc-{scenario.service}-{uuid.uuid4().hex[:6]}"
    mode = _mode(body.llm_mode)
    inp = IncidentInput(
        incident_id=incident_id,
        service=scenario.service,
        alert=scenario.alert,
        severity=scenario.severity,
        scenario=scenario.key,
        llm_mode=mode,
        monitor_checks=scenario.monitor_checks,
        monitor_interval_seconds=scenario.monitor_interval_seconds,
        approval_timeout_seconds=scenario.approval_timeout_seconds,
        idle_timeout_seconds=scenario.idle_timeout_seconds,
    )
    await _client().start_workflow(
        IncidentWorkflow.run,
        inp,
        id=incident_id,
        task_queue=AGENT_TASK_QUEUE,
        search_attributes=TypedSearchAttributes([
            SearchAttributePair(sa.INCIDENT_SERVICE, scenario.service),
            SearchAttributePair(sa.INCIDENT_SEVERITY, scenario.severity),
            SearchAttributePair(sa.DEMO_SCENARIO, scenario.key),
            SearchAttributePair(sa.AGENT_PHASE, Phases.STARTING),
            SearchAttributePair(sa.APPROVAL_STATUS, ApprovalStatuses.NOT_REQUIRED),
        ]),
    )
    return {"workflow_id": incident_id, "llm_mode": mode, "fault_armed": fault_armed}


@app.post("/api/triage")
async def start_triage(body: StartTriageBody) -> dict[str, Any]:
    triage_id = f"triage-{body.service}-{uuid.uuid4().hex[:6]}"
    mode = _mode(body.llm_mode)
    await _client().start_workflow(
        TriageWorkflow.run,
        TriageInput(
            incident_id=triage_id,
            service=body.service,
            alert=TRIAGE_ALERTS.get(body.service, "Anomaly detected"),
            llm_mode=mode,
        ),
        id=triage_id,
        task_queue=AGENT_TASK_QUEUE,
        search_attributes=TypedSearchAttributes([
            SearchAttributePair(sa.INCIDENT_SERVICE, body.service),
            SearchAttributePair(sa.INCIDENT_SEVERITY, "SEV3"),
            SearchAttributePair(sa.DEMO_SCENARIO, "triage"),
            SearchAttributePair(sa.AGENT_PHASE, Phases.STARTING),
        ]),
    )
    return {"workflow_id": triage_id, "llm_mode": mode}


@app.get("/api/runs")
async def runs() -> dict[str, Any]:
    return {"runs": await list_runs(_client())}


@app.get("/api/runs/{workflow_id}/snapshot")
async def snapshot(workflow_id: str) -> dict[str, Any]:
    try:
        return await app.state.snapshots.build(workflow_id)
    except RPCError as e:
        raise HTTPException(404, f"Could not load {workflow_id}: {e}") from e


@app.post("/api/runs/{workflow_id}/approval")
async def approval(workflow_id: str, body: ApprovalBody) -> dict[str, Any]:
    handle = _client().get_workflow_handle(workflow_id)
    try:
        result = await handle.execute_update(
            IncidentWorkflow.submit_approval,
            ApprovalDecision(approved=body.approved, reviewer=body.reviewer.strip() or "on-call", comment=body.comment),
            rpc_timeout=timedelta(seconds=15),
        )
    except WorkflowUpdateFailedError as e:
        raise HTTPException(409, str(e.cause or e)) from e
    except RPCError as e:
        raise HTTPException(503, f"No worker accepted the update in time: {e}") from e
    return {"result": result}


@app.post("/api/runs/{workflow_id}/messages")
async def message(workflow_id: str, body: MessageBody) -> dict[str, Any]:
    if not body.text.strip():
        raise HTTPException(400, "Message is empty")
    await _client().get_workflow_handle(workflow_id).signal(IncidentWorkflow.send_message, body.text)
    return {"ok": True}


@app.post("/api/sessions/close-all")
async def close_all_sessions() -> dict[str, Any]:
    """Ask every running incident session to close. Sessions mid-approval or stuck
    on the workflow bug finish that first (or terminate them from the CLI)."""
    signaled: list[str] = []
    query = 'WorkflowType = "IncidentWorkflow" AND ExecutionStatus = "Running"'
    async for wf in _client().list_workflows(query):
        try:
            await _client().get_workflow_handle(wf.id).signal(IncidentWorkflow.close_incident)
            signaled.append(wf.id)
        except RPCError:
            continue
    return {"signaled": signaled}


@app.post("/api/runs/{workflow_id}/close")
async def close(workflow_id: str) -> dict[str, Any]:
    await _client().get_workflow_handle(workflow_id).signal(IncidentWorkflow.close_incident)
    return {"ok": True}


async def _arm_fault(fault: dict[str, Any]) -> bool:
    try:
        async with httpx.AsyncClient(timeout=3.0) as http:
            r = await http.post(f"{settings.mcp_admin_url}/faults", json=fault)
            r.raise_for_status()
        log.info("Armed MCP fault %s", fault)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("Could not arm MCP fault %s: %s", fault, e)
        return False


def cli() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(app, host=settings.api_host, port=settings.api_port, log_level="info")


if __name__ == "__main__":
    cli()
