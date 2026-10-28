"""Small helpers so the workflows run on a range of temporalio versions.

Newer SDKs accept a human-readable ``summary`` on activities and timers, which the
Temporal UI shows next to each event. Older SDKs don't, so we only pass it when the
installed SDK supports it. Signature inspection is deterministic, so this is safe
inside the workflow sandbox.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import timedelta
from typing import Any

from temporalio import workflow

_ACTIVITY_SUPPORTS_SUMMARY = "summary" in inspect.signature(workflow.execute_activity).parameters
_SLEEP = getattr(workflow, "sleep", None)
_SLEEP_SUPPORTS_SUMMARY = _SLEEP is not None and "summary" in inspect.signature(_SLEEP).parameters


def activity_meta(summary: str) -> dict[str, Any]:
    """Keyword arguments to add a UI summary to an activity, when supported."""
    return {"summary": summary[:200]} if _ACTIVITY_SUPPORTS_SUMMARY else {}


async def durable_sleep(seconds: float, summary: str) -> None:
    """A durable timer. Survives worker restarts; recorded as TimerStarted/TimerFired."""
    if _SLEEP is None:
        await asyncio.sleep(seconds)
    elif _SLEEP_SUPPORTS_SUMMARY:
        await _SLEEP(timedelta(seconds=seconds), summary=summary[:200])
    else:
        await _SLEEP(timedelta(seconds=seconds))
