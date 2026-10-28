"""Preflight check: is everything the demo needs running and connected?

    uv run incident-doctor                              # check everything
    uv run incident-doctor --register-search-attributes # also create missing attributes
    uv run incident-doctor --live-llm                   # also make one tiny Claude call
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from incident_agent.config import settings
from incident_agent.shared.constants import AGENT_TASK_QUEUE, CHANGE_TASK_QUEUE
from incident_agent.shared.search_attributes import DEFINITIONS
from incident_agent.temporal_client import connect

OK, WARN, FAIL = "ok", "warn", "FAIL"


async def main(register: bool, live_llm: bool) -> int:
    results: list[tuple[str, str, str]] = []

    try:
        client = await connect()
        results.append(("Temporal", OK, f"{settings.temporal_address} namespace {settings.temporal_namespace}"))
    except Exception as e:  # noqa: BLE001
        results.append(("Temporal", FAIL, f"cannot connect to {settings.temporal_address}: {e}"))
        return _print(results)

    results.append(await _search_attributes(client, register))
    results.append(await _pollers(client, AGENT_TASK_QUEUE, "Python worker"))
    results.append(await _pollers(client, CHANGE_TASK_QUEUE, "Java worker"))
    results.extend(await _mcp())
    results.append(await _llm(live_llm))
    return _print(results)


async def _search_attributes(client: Any, register: bool) -> tuple[str, str, str]:
    from temporalio.api.enums.v1 import IndexedValueType
    from temporalio.api.operatorservice.v1 import AddSearchAttributesRequest, ListSearchAttributesRequest

    try:
        resp = await client.operator_service.list_search_attributes(
            ListSearchAttributesRequest(namespace=settings.temporal_namespace)
        )
        missing = [name for name in DEFINITIONS if name not in resp.custom_attributes]
        if not missing:
            return ("Search attributes", OK, ", ".join(DEFINITIONS))
        if not register:
            return ("Search attributes", FAIL, f"missing {missing}; rerun with --register-search-attributes")
        types = {
            "Keyword": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
            "Int": IndexedValueType.INDEXED_VALUE_TYPE_INT,
        }
        await client.operator_service.add_search_attributes(
            AddSearchAttributesRequest(
                namespace=settings.temporal_namespace,
                search_attributes={name: types[DEFINITIONS[name]] for name in missing},
            )
        )
        return ("Search attributes", OK, f"registered {missing}")
    except Exception as e:  # noqa: BLE001
        return ("Search attributes", WARN, f"could not check ({e}). On Temporal Cloud, add them in the Cloud UI or tcld.")


async def _pollers(client: Any, task_queue: str, label: str) -> tuple[str, str, str]:
    from temporalio.api.enums.v1 import TaskQueueType
    from temporalio.api.taskqueue.v1 import TaskQueue
    from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest

    try:
        resp = await client.workflow_service.describe_task_queue(
            DescribeTaskQueueRequest(
                namespace=settings.temporal_namespace,
                task_queue=TaskQueue(name=task_queue),
                task_queue_type=TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW,
            )
        )
        if resp.pollers:
            ids = ", ".join(p.identity for p in resp.pollers[:2])
            return (label, OK, f"polling '{task_queue}' ({ids})")
        return (label, FAIL, f"nothing polling '{task_queue}'. Start it.")
    except Exception as e:  # noqa: BLE001
        return (label, WARN, f"could not describe '{task_queue}': {e}")


async def _mcp() -> list[tuple[str, str, str]]:
    import httpx
    from langchain_mcp_adapters.client import MultiServerMCPClient

    out: list[tuple[str, str, str]] = []
    try:
        client = MultiServerMCPClient({"ops": {"url": settings.mcp_url, "transport": "streamable_http"}})
        tools = await client.get_tools()
        out.append(("MCP server", OK, f"{settings.mcp_url}: {', '.join(t.name for t in tools)}"))
    except Exception as e:  # noqa: BLE001
        out.append(("MCP server", FAIL, f"{settings.mcp_url} unreachable: {e}"))
    try:
        async with httpx.AsyncClient(timeout=3.0) as http:
            r = await http.get(f"{settings.mcp_admin_url}/faults")
            r.raise_for_status()
        out.append(("MCP fault admin", OK, f"{settings.mcp_admin_url}/faults"))
    except Exception as e:  # noqa: BLE001
        out.append(("MCP fault admin", WARN, f"API downtime scenario needs {settings.mcp_admin_url}/faults: {e}"))
    return out


async def _llm(live: bool) -> tuple[str, str, str]:
    label = f"LLM ({settings.llm_label})"
    missing = settings.llm_missing_env()
    if missing:
        return (label, WARN, f"{', '.join(missing)} not set for LLM_PROVIDER={settings.llm_provider}; only Scripted mode will work")
    if not settings.llm_model:
        return (label, WARN, f"LLM_MODEL not set for LLM_PROVIDER={settings.llm_provider}; only Scripted mode will work")
    if not live:
        return (label, OK, f"{settings.llm_model} via {settings.chat_model_class} (use --live-llm to test a call)")
    try:
        from incident_agent.activities.llm import build_chat_model

        reply = await build_chat_model(max_tokens=16).ainvoke("Reply with the single word OK.")
        return (label, OK, f"{settings.llm_model} replied: {str(reply.content)[:40]}")
    except Exception as e:  # noqa: BLE001
        return (label, FAIL, f"{settings.llm_model}: {type(e).__name__}: {e}")


def _print(results: list[tuple[str, str, str]]) -> int:
    width = max(len(r[0]) for r in results) + 2
    for name, status, detail in results:
        print(f"[{status:>4}] {name.ljust(width)}{detail}")
    return 1 if any(r[1] == FAIL for r in results) else 0


def cli() -> None:
    parser = argparse.ArgumentParser(description="Preflight checks for the incident copilot demo")
    parser.add_argument("--register-search-attributes", action="store_true")
    parser.add_argument("--live-llm", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.register_search_attributes, args.live_llm)))


if __name__ == "__main__":
    cli()
