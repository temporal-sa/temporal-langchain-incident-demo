"""MCP activities, built on langchain-mcp-adapters.

LangChain's MCP adapter turns the server's tools into LangChain tools. We use it
twice: once to discover the tool catalog (recorded in history), and once per tool
call. Each call is a Temporal activity, so an MCP outage is just a retry, and a
completed call is never re-executed on replay.

Error handling splits two cases:

* transport or server unavailability -> raise (retryable): Temporal retries.
* a tool-level answer such as "service not found" -> return is_error=True so the
  model can read it and adjust. Retrying would not change the answer.
"""

from __future__ import annotations

import json
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

from incident_agent.config import settings
from incident_agent.shared.constants import Activities
from incident_agent.shared.models import (
    McpDiscoveryRequest,
    McpToolCatalog,
    McpToolRequest,
    McpToolResult,
    ToolSpec,
)

_TRANSIENT_MARKERS = ("503", "unavailable", "timed out")


def _client():
    from langchain_mcp_adapters.client import MultiServerMCPClient

    return MultiServerMCPClient({"ops": {"url": settings.mcp_url, "transport": "streamable_http"}})


async def _load_tools() -> list[Any]:
    try:
        return await _client().get_tools()
    except Exception as e:  # transport errors arrive wrapped in exception groups
        raise ApplicationError(
            f"MCP server unreachable at {settings.mcp_url}: {_flatten(e)}",
            type="McpUnavailable",
        ) from e


@activity.defn(name=Activities.DISCOVER_MCP_TOOLS)
async def discover_mcp_tools(req: McpDiscoveryRequest) -> McpToolCatalog:
    tools = await _load_tools()
    specs = [
        ToolSpec(
            name=t.name,
            description=t.description or "",
            input_schema=_schema_of(t),
            source="mcp",
        )
        for t in tools
    ]
    activity.logger.info("Discovered %d MCP tools from %s", len(specs), settings.mcp_url)
    return McpToolCatalog(server_url=settings.mcp_url, tools=specs)


@activity.defn(name=Activities.CALL_MCP_TOOL)
async def call_mcp_tool(req: McpToolRequest) -> McpToolResult:
    from langchain_core.tools import ToolException

    attempt = activity.info().attempt
    activity.logger.info("MCP call %s %s (attempt %d)", req.tool, req.args, attempt)

    tools = {t.name: t for t in await _load_tools()}
    tool = tools.get(req.tool)
    if tool is None:
        return McpToolResult(tool=req.tool, content=f"MCP tool '{req.tool}' does not exist.", is_error=True)

    try:
        raw = await tool.ainvoke(req.args)
    except ToolException as e:
        message = str(e)
        if any(marker in message.lower() for marker in _TRANSIENT_MARKERS):
            raise ApplicationError(
                f"MCP {req.tool} failed on attempt {attempt}: {message}",
                type="McpUnavailable",
            ) from e
        return McpToolResult(tool=req.tool, content=message, is_error=True)
    except Exception as e:
        raise ApplicationError(
            f"MCP {req.tool} transport error on attempt {attempt}: {_flatten(e)}",
            type="McpUnavailable",
        ) from e

    return McpToolResult(tool=req.tool, content=_to_text(raw))


# --------------------------------------------------------------------------- #
# Helpers that tolerate differences between langchain-mcp-adapters versions     #
# --------------------------------------------------------------------------- #


def _schema_of(tool: Any) -> dict[str, Any]:
    schema = getattr(tool, "args_schema", None)
    if isinstance(schema, dict):
        return schema
    if schema is not None and hasattr(schema, "model_json_schema"):
        return schema.model_json_schema()
    return {"type": "object", "properties": {}}


def _to_text(raw: Any) -> str:
    if isinstance(raw, tuple) and raw:
        raw = raw[0]
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts = []
        for item in raw:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and "text" in item:
                parts.append(str(item["text"]))
            elif hasattr(item, "text"):
                parts.append(str(item.text))
            else:
                parts.append(json.dumps(item, default=str))
        return "\n".join(parts)
    return json.dumps(raw, default=str)


def _flatten(error: BaseException) -> str:
    nested = getattr(error, "exceptions", None)
    if nested:
        return "; ".join(_flatten(e) for e in nested)
    return f"{type(error).__name__}: {error}"
