"""Python worker: hosts the agent workflows and the LangChain / MCP / tool activities.

    uv run incident-worker

Stop it at any point (Ctrl-C, or kill -9) to demonstrate durable execution:
running incidents pause, nothing is lost, and they resume when the worker returns.
"""

from __future__ import annotations

import asyncio
import logging

from temporalio.worker import Worker

from incident_agent.activities import ALL_ACTIVITIES
from incident_agent.config import settings
from incident_agent.shared.constants import AGENT_TASK_QUEUE
from incident_agent.temporal_client import connect
from incident_agent.workflows import ALL_WORKFLOWS


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    client = await connect()
    worker = Worker(
        client,
        task_queue=AGENT_TASK_QUEUE,
        workflows=ALL_WORKFLOWS,
        activities=ALL_ACTIVITIES,
    )
    logging.info(
        "Python worker polling task queue '%s' on %s (namespace %s). LLM: %s %s. MCP: %s",
        AGENT_TASK_QUEUE, settings.temporal_address, settings.temporal_namespace,
        settings.llm_provider, settings.llm_model or "(LLM_MODEL not set)", settings.mcp_url,
    )
    await worker.run()


def cli() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    cli()
