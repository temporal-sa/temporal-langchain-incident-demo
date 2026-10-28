"""Translate between the workflow's framework-neutral conversation and LangChain.

The workflow stores ChatMessage dataclasses (plain JSON in history). Only activity
code turns them into LangChain message objects. That boundary keeps LangChain out
of the deterministic workflow sandbox and lets you swap frameworks later without
touching workflow code.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from incident_agent.shared.models import ChatMessage, ToolCall, ToolSpec


def to_langchain_messages(system_prompt: str, messages: list[ChatMessage]) -> list[BaseMessage]:
    out: list[BaseMessage] = [SystemMessage(content=system_prompt)]
    for m in messages:
        if m.role == "user":
            out.append(HumanMessage(content=m.content or "(empty message)"))
        elif m.role == "assistant":
            blocks: list[Any] = [{"type": "text", "text": m.content}] if m.content.strip() else []
            if not blocks and not m.tool_calls:
                blocks = [{"type": "text", "text": "(no reply)"}]
            out.append(
                AIMessage(
                    content=blocks,
                    tool_calls=[{"id": tc.id, "name": tc.name, "args": tc.args} for tc in m.tool_calls],
                )
            )
        elif m.role == "tool":
            out.append(
                ToolMessage(
                    content=m.content or "(empty result)",
                    tool_call_id=m.tool_call_id or "",
                    status="error" if m.is_error else "success",
                )
            )
    return out


def to_anthropic_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    """Anthropic-format tool definitions; ChatAnthropic.bind_tools accepts these as-is."""
    return [
        {"name": t.name, "description": t.description, "input_schema": _clean_schema(t.input_schema)}
        for t in tools
    ]


def to_openai_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    """OpenAI-style function tools: the format every LangChain chat model's bind_tools accepts."""
    return [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": _clean_schema(t.input_schema)},
        }
        for t in tools
    ]


def extract_text(content: Any) -> str:
    """Text from a LangChain message content (str or list of content blocks)."""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content or []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "\n".join(p for p in parts if p).strip()


def extract_tool_calls(message: AIMessage) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for tc in getattr(message, "tool_calls", None) or []:
        args = tc.get("args") or {}
        if not isinstance(args, dict):
            try:
                args = json.loads(args)
            except (TypeError, ValueError):
                args = {"value": args}
        calls.append(ToolCall(id=str(tc.get("id") or ""), name=str(tc.get("name") or ""), args=args))
    return calls


def _clean_schema(schema: dict[str, Any]) -> dict[str, Any]:
    cleaned = {k: v for k, v in (schema or {}).items() if k not in ("$schema",)}
    cleaned.setdefault("type", "object")
    cleaned.setdefault("properties", {})
    return cleaned
