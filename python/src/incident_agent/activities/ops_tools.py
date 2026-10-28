"""Tools we own, implemented as Temporal activities.

These stand in for real systems (ticketing, paging, monitoring). Every side effect
is appended to a journal file, so you can confirm that a worker crash
did not page anyone twice:

    tail -f python/.demo-data/side-effects.jsonl
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any

from temporalio import activity

from incident_agent.config import settings
from incident_agent.shared.constants import Activities
from incident_agent.shared.models import (
    HealthCheckRequest,
    HealthCheckResult,
    OncallPage,
    Ticket,
    TicketNote,
    TicketRequest,
)


@activity.defn(name=Activities.OPEN_TICKET)
async def open_ticket(req: TicketRequest) -> Ticket:
    # Deterministic id from the incident id makes the activity idempotent on retry.
    ticket_id = "TKT-" + hashlib.sha1(req.incident_id.encode()).hexdigest()[:6].upper()
    await _journal("ticket.opened", ticket_id=ticket_id, service=req.service, severity=req.severity, title=req.title)
    return Ticket(ticket_id=ticket_id, url=f"https://tickets.example.com/{ticket_id}")


@activity.defn(name=Activities.ADD_TICKET_NOTE)
async def add_ticket_note(note: TicketNote) -> str:
    await _journal("ticket.note", ticket_id=note.ticket_id, note=note.note)
    return f"Note added to {note.ticket_id}."


@activity.defn(name=Activities.UPDATE_TICKET)
async def update_ticket(note: TicketNote) -> str:
    await _journal("ticket.updated", ticket_id=note.ticket_id, status=note.status, note=note.note[:500])
    return f"Ticket {note.ticket_id} set to {note.status or 'updated'}."


@activity.defn(name=Activities.NOTIFY_ONCALL)
async def notify_oncall(page: OncallPage) -> str:
    await _journal("oncall.paged", incident_id=page.incident_id, service=page.service, urgency=page.urgency, message=page.message)
    return f"Paged on-call for {page.service} ({page.urgency})."


# Simulated post-change metrics per service: (degraded error %, baseline error %, degraded p95, baseline p95)
_BASELINES: dict[str, tuple[float, float, int, int]] = {
    "checkout-api": (4.2, 0.3, 2400, 240),
    "payments-api": (18.0, 0.2, 900, 180),
    "inventory-api": (9.5, 0.4, 3100, 210),
    "search-api": (6.0, 0.5, 1800, 160),
    "orders-api": (3.1, 0.3, 1500, 220),
    "legacy-billing": (7.0, 0.6, 2600, 400),
}


@activity.defn(name=Activities.CHECK_SERVICE_HEALTH)
async def check_service_health(req: HealthCheckRequest) -> HealthCheckResult:
    await asyncio.sleep(0.5)  # pretend to query monitoring
    bad_err, good_err, bad_p95, good_p95 = _BASELINES.get(req.service, (5.0, 0.5, 2000, 250))
    progress = min(1.0, req.check_number / max(1, req.total_checks))
    error_rate = round(bad_err + (good_err - bad_err) * progress, 2)
    p95 = int(bad_p95 + (good_p95 - bad_p95) * progress)
    healthy = progress >= 1.0
    summary = f"check {req.check_number}/{req.total_checks}: errors {error_rate}%, p95 {p95} ms"
    await _journal("health.checked", incident_id=req.incident_id, service=req.service, summary=summary)
    return HealthCheckResult(healthy=healthy, error_rate_pct=error_rate, p95_latency_ms=p95, summary=summary)


async def _journal(kind: str, **fields: Any) -> None:
    info = activity.info()
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "kind": kind,
        "workflow_id": info.workflow_id,
        "activity": info.activity_type,
        "attempt": info.attempt,
        **fields,
    }
    activity.logger.info("%s %s", kind, json.dumps(fields, default=str)[:300])

    def _write() -> None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        with (settings.data_dir / "side-effects.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")

    await asyncio.to_thread(_write)
