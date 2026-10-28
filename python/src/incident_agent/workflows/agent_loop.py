"""The agent's inner loop, orchestrated durably.

LangChain does the reasoning work inside the ``call_llm`` activity: it formats the
conversation, binds the tool schemas, calls Claude, and parses tool calls. This
module decides what happens with that answer. Each model decision and each tool
call is a separate activity, so:

* a crash between any two steps resumes from event history without repeating
  completed LLM calls or tool calls,
* every step gets its own retry policy and timeout, and
* the whole loop is visible step by step in the Temporal UI and the demo console.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Awaitable, Callable

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from incident_agent.shared.constants import Activities
    from incident_agent.shared.models import (
        ChatMessage,
        LlmRequest,
        LlmResponse,
        McpToolRequest,
        McpToolResult,
        OncallPage,
        SessionState,
        TicketNote,
        ToolCall,
    )
    from incident_agent.shared.tools import ACTIVITY_TOOL_ROUTES

from incident_agent.workflows._compat import activity_meta

# Retries live here, not in the LLM client (the client is configured with max_retries=0).
LLM_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=6,
    non_retryable_error_types=["LlmAuthError", "LlmBadRequest", "LlmConfigError"],
)

# Tuned for the "API downtime" scenario: four fast failures, success on attempt five.
MCP_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=1.5,
    maximum_interval=timedelta(seconds=4),
    maximum_attempts=10,
)

TOOL_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=5,
)

WorkflowToolHandler = Callable[[ToolCall], Awaitable[tuple[str, bool]]]


class AgentLoop:
    def __init__(self, state: SessionState, max_iterations: int = 8) -> None:
        self.state = state
        self.max_iterations = max_iterations

    async def run_turn(
        self,
        *,
        purpose: str,
        system_prompt: str,
        workflow_tools: dict[str, WorkflowToolHandler],
    ) -> str:
        """Run one agent turn: think, act, observe, until the model replies with text."""
        st = self.state
        st.turn += 1
        st.agent_busy = True
        try:
            for iteration in range(self.max_iterations):
                response: LlmResponse = await workflow.execute_activity(
                    Activities.CALL_LLM,
                    LlmRequest(
                        purpose=purpose,
                        llm_mode=st.llm_mode,
                        system_prompt=system_prompt,
                        messages=list(st.messages),
                        tools=list(st.tools),
                        context={
                            "incident_id": st.incident_id,
                            "service": st.service,
                            "severity": st.severity,
                            "scenario": st.scenario,
                        },
                        turn=st.turn,
                        iteration=iteration,
                    ),
                    result_type=LlmResponse,
                    start_to_close_timeout=timedelta(seconds=120),
                    retry_policy=LLM_RETRY,
                    **activity_meta(f"Claude via LangChain: turn {st.turn}, step {iteration + 1}"),
                )
                st.llm_calls += 1
                st.input_tokens += response.input_tokens
                st.output_tokens += response.output_tokens
                st.messages.append(
                    ChatMessage(
                        role="assistant",
                        content=response.content,
                        tool_calls=list(response.tool_calls),
                        turn=st.turn,
                    )
                )

                if not response.tool_calls:
                    return response.content

                # Independent tool calls run concurrently; each is its own durable step.
                results = await asyncio.gather(
                    *(self._dispatch(call, workflow_tools) for call in response.tool_calls)
                )
                for call, (text, is_error) in zip(response.tool_calls, results):
                    st.messages.append(
                        ChatMessage(
                            role="tool",
                            content=text,
                            tool_call_id=call.id,
                            tool_name=call.name,
                            is_error=is_error,
                            turn=st.turn,
                        )
                    )

            note = "I reached my step limit for this turn. Ask me to continue if you need more."
            st.messages.append(ChatMessage(role="assistant", content=note, turn=st.turn))
            return note
        finally:
            st.agent_busy = False

    async def _dispatch(
        self, call: ToolCall, workflow_tools: dict[str, WorkflowToolHandler]
    ) -> tuple[str, bool]:
        st = self.state
        st.tool_calls += 1
        args = call.args if isinstance(call.args, dict) else {}

        # 1. Tools implemented in workflow code (policy, approval, child workflow).
        if call.name in workflow_tools:
            return await workflow_tools[call.name](call)

        spec = next((t for t in st.tools if t.name == call.name), None)
        if spec is None:
            return f"Unknown tool '{call.name}'. Use one of the tools provided.", True

        # 2. Tools discovered from the MCP server, called through an activity.
        if spec.source == "mcp":
            result: McpToolResult = await workflow.execute_activity(
                Activities.CALL_MCP_TOOL,
                McpToolRequest(tool=call.name, args=args, incident_id=st.incident_id),
                result_type=McpToolResult,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=MCP_RETRY,
                **activity_meta(f"MCP {call.name} for {args.get('service', st.service)}"),
            )
            return result.content, result.is_error

        # 3. Side-effecting tools we own, each a Temporal activity.
        route = ACTIVITY_TOOL_ROUTES.get(call.name)
        if spec.source == "activity" and route is not None:
            if route == Activities.NOTIFY_ONCALL:
                payload: object = OncallPage(
                    incident_id=st.incident_id,
                    service=st.service,
                    message=str(args.get("message", "")),
                    urgency=str(args.get("urgency", "high")),
                )
            else:
                payload = TicketNote(
                    ticket_id=st.ticket_id or "unassigned",
                    note=str(args.get("note", "")),
                )
            text: str = await workflow.execute_activity(
                route,
                payload,
                result_type=str,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=TOOL_RETRY,
                **activity_meta(f"Tool {call.name}"),
            )
            return text, False

        return f"Tool '{call.name}' is not available in this session.", True
