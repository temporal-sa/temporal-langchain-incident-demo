# mcp-server/: the ops-tools MCP server

A standalone [Model Context Protocol](https://modelcontextprotocol.io) server, built with the official Python SDK (FastMCP) over streamable HTTP. It plays the part of someone else's system: the agent knows nothing about its tools until it asks at run time.

```bash
uv sync
uv run ops-mcp          # http://127.0.0.1:8765/mcp
```

## Tools

| Tool | Returns |
|---|---|
| `get_service_info(service)` | Owner, tier, current version, runbook, dependencies |
| `get_metrics(service, window_minutes)` | Error rate, p95 latency, CPU, memory, and a trend note |
| `search_logs(service, query, window_minutes, limit)` | Matching log lines |
| `get_recent_deploys(service, limit)` | Recent deploys, newest first |

The data is canned (`src/ops_mcp/data.py`). Each service's data points toward one remediation, which is what makes each scenario repeatable. An unknown service returns a tool error the model can read.

## Fault injection

Used by the API downtime scenario; the console arms it for you.

```bash
curl -X POST localhost:8765/admin/faults -H 'content-type: application/json' \
     -d '{"tool": "get_metrics", "service": "inventory-api", "fail_times": 4}'
curl localhost:8765/admin/faults            # armed faults and recent calls
curl -X DELETE localhost:8765/admin/faults  # clear
```

`/healthz` reports whether the server is up. Set `MCP_HOST` and `MCP_PORT` to change where it listens; if you change the port, also set `MCP_BASE_URL` in `.env`.
