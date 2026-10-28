"""Ops-tools MCP server.

A standalone MCP server (official Python SDK, FastMCP) exposing read-only
operational data over streamable HTTP. The agent discovers these tools at run
time through LangChain's MCP adapters; it has no compile-time knowledge of them.

    uv run ops-mcp                  # http://127.0.0.1:8765/mcp

Fault injection (used by the "API downtime" scenario; the demo API arms it for you):

    curl -X POST localhost:8765/admin/faults \\
         -H 'content-type: application/json' \\
         -d '{"tool": "get_metrics", "service": "inventory-api", "fail_times": 4}'
    curl localhost:8765/admin/faults          # show armed faults and recent calls
    curl -X DELETE localhost:8765/admin/faults
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import deque

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from ops_mcp import data

HOST = os.getenv("MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("MCP_PORT", "8765"))

log = logging.getLogger("ops_mcp")

mcp = FastMCP(
    "ops-tools",
    instructions="Read-only operational data: service catalog, metrics, logs, and deploy history.",
    host=HOST,
    port=PORT,
    stateless_http=True,
    json_response=True,
)

# (tool, service) -> failures remaining. service "*" matches any service.
FAULTS: dict[tuple[str, str], int] = {}
CALLS: deque[dict] = deque(maxlen=50)


def _record(tool: str, service: str, outcome: str) -> None:
    CALLS.append({"ts": time.strftime("%H:%M:%S"), "tool": tool, "service": service, "outcome": outcome})
    log.info("%s(%s) -> %s", tool, service, outcome)


def _guard(tool: str, service: str) -> None:
    """Raise a simulated outage if a fault is armed, and reject unknown services."""
    for key in ((tool, service), (tool, "*")):
        remaining = FAULTS.get(key, 0)
        if remaining > 0:
            FAULTS[key] = remaining - 1
            _record(tool, service, f"simulated 503 ({remaining - 1} failures left)")
            raise RuntimeError(
                f"503 Service Unavailable: metrics backend for {service} timed out "
                f"(simulated outage, {remaining - 1} failures left)"
            )
    if service not in data.SERVICES:
        _record(tool, service, "unknown service")
        known = ", ".join(sorted(data.SERVICES))
        raise ValueError(f"Service '{service}' not found in the service catalog. Known services: {known}")


@mcp.tool()
def get_service_info(service: str) -> str:
    """Look up a service in the service catalog: owner, tier, current version, runbook, and dependencies."""
    _guard("get_service_info", service)
    _record("get_service_info", service, "ok")
    return json.dumps({"service": service, **data.SERVICES[service]})


@mcp.tool()
def get_metrics(service: str, window_minutes: int = 30) -> str:
    """Current golden-signal metrics for a service (error rate, p95 latency, CPU, memory) with a trend note."""
    _guard("get_metrics", service)
    _record("get_metrics", service, "ok")
    return json.dumps({"service": service, "window_minutes": window_minutes, **data.METRICS[service]})


@mcp.tool()
def search_logs(service: str, query: str = "error", window_minutes: int = 30, limit: int = 8) -> str:
    """Search recent log lines for a service. Matching is case-insensitive on any word in the query."""
    _guard("search_logs", service)
    lines = data.LOGS[service]
    terms = [t.lower() for t in query.replace(" OR ", " ").split() if t]
    matched = [line for line in lines if any(t in line.lower() for t in terms)] or lines
    _record("search_logs", service, f"{len(matched)} lines")
    return json.dumps({"service": service, "query": query, "window_minutes": window_minutes,
                       "matches": len(matched), "lines": matched[:limit]})


@mcp.tool()
def get_recent_deploys(service: str, limit: int = 5) -> str:
    """Recent deploys for a service, newest first, with minutes since each deploy."""
    _guard("get_recent_deploys", service)
    _record("get_recent_deploys", service, "ok")
    return json.dumps({"service": service, "current_version": data.SERVICES[service]["current_version"],
                       "deploys": data.DEPLOYS[service][:limit]})


@mcp.custom_route("/admin/faults", methods=["GET", "POST", "DELETE"])
async def faults(request: Request) -> JSONResponse:
    if request.method == "POST":
        body = await request.json()
        key = (str(body.get("tool", "get_metrics")), str(body.get("service", "*")))
        FAULTS[key] = int(body.get("fail_times", 4))
        log.info("Armed fault %s x%d", key, FAULTS[key])
    elif request.method == "DELETE":
        FAULTS.clear()
    return JSONResponse({
        "faults": [{"tool": t, "service": s, "remaining": n} for (t, s), n in FAULTS.items() if n > 0],
        "recent_calls": list(CALLS)[-20:],
    })


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "tools": 4})


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log.info("ops-tools MCP server on http://%s:%d/mcp", HOST, PORT)
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
