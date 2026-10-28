"""TriageWorkflow: the short-lived counterpart to IncidentWorkflow.

Same durable inner loop, but only read-only MCP tools, a small step budget, and no
human gate. Finishes in seconds. Useful for contrasting a short workflow with the
long-running incident session.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from incident_agent.shared import search_attributes as sa
    from incident_agent.shared.constants import Activities, Phases
    from incident_agent.shared.models import (
        ChatMessage,
        McpDiscoveryRequest,
        McpToolCatalog,
        SessionState,
        TriageInput,
        TriageReport,
    )
    from incident_agent.shared.prompts import TRIAGE_SYSTEM

from incident_agent.workflows._compat import activity_meta
from incident_agent.workflows.agent_loop import MCP_RETRY, AgentLoop


@workflow.defn
class TriageWorkflow:
    @workflow.init
    def __init__(self, inp: TriageInput) -> None:
        self._state = SessionState(
            incident_id=inp.incident_id,
            service=inp.service,
            severity=inp.severity,
            scenario=inp.scenario,
            llm_mode=inp.llm_mode,
        )
        self._agent = AgentLoop(self._state, max_iterations=4)

    @workflow.run
    async def run(self, inp: TriageInput) -> TriageReport:
        st = self._state
        self._set_phase(Phases.TRIAGING)

        catalog: McpToolCatalog = await workflow.execute_activity(
            Activities.DISCOVER_MCP_TOOLS,
            McpDiscoveryRequest(incident_id=inp.incident_id),
            result_type=McpToolCatalog,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=MCP_RETRY,
            **activity_meta("Discover MCP tools via LangChain MCP adapters"),
        )
        st.tools = list(catalog.tools)  # read-only: no remediation, no paging

        st.messages.append(
            ChatMessage(
                role="user",
                author="alert",
                turn=1,
                content=f"ALERT [{inp.severity}] {inp.service}: {inp.alert}\nTriage this quickly.",
            )
        )
        summary = await self._agent.run_turn(
            purpose="triage",
            system_prompt=TRIAGE_SYSTEM.format(incident_id=inp.incident_id, service=inp.service),
            workflow_tools={},
        )
        st.summary = summary
        self._set_phase(Phases.TRIAGED)
        workflow.upsert_search_attributes(
            [sa.LLM_TOKENS.value_set(st.input_tokens + st.output_tokens)]
        )
        return TriageReport(
            incident_id=st.incident_id,
            service=st.service,
            summary=summary,
            llm_calls=st.llm_calls,
            tool_calls=st.tool_calls,
            total_tokens=st.input_tokens + st.output_tokens,
        )

    def _set_phase(self, phase: str) -> None:
        self._state.phase = phase
        workflow.upsert_search_attributes([sa.AGENT_PHASE.value_set(phase)])

    @workflow.query
    def get_state(self) -> SessionState:
        return self._state
