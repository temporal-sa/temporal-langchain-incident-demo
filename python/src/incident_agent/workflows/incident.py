"""IncidentWorkflow: the outer loop.

This workflow owns the incident session for as long as it lives: minutes for a
happy path, hours if a human takes a while to approve, days if engineers keep
asking follow-up questions. It owns:

* the conversation and every tool result (rebuilt from history on any restart),
* change policy and the human approval gate (a validated Update),
* the cross-language hop to the Java change-management workflow,
* durable timers for post-change verification, and
* search attributes for Advanced Visibility.

LangChain never sees any of this. It only ever sees one LLM call at a time.
"""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import timedelta
from typing import Optional

from temporalio import workflow
from temporalio.exceptions import ApplicationError, ChildWorkflowError

with workflow.unsafe.imports_passed_through():
    from incident_agent.shared import search_attributes as sa
    from incident_agent.shared.constants import (
        CHANGE_TASK_QUEUE,
        CHANGE_WORKFLOW_TYPE,
        Activities,
        ApprovalStatuses,
        Phases,
    )
    from incident_agent.shared.models import (
        ApprovalDecision,
        ChangeRequest,
        ChangeResult,
        ChatMessage,
        HealthCheckRequest,
        HealthCheckResult,
        IncidentInput,
        IncidentReport,
        McpDiscoveryRequest,
        McpToolCatalog,
        OncallPage,
        RemediationProposal,
        SessionState,
        Ticket,
        TicketNote,
        TicketRequest,
        ToolCall,
    )
    from incident_agent.shared.prompts import FOLLOW_UP_SYSTEM, INVESTIGATE_SYSTEM
    from incident_agent.shared.tools import HIGH_RISK_ACTIONS, NATIVE_TOOLS, VALID_ACTIONS

from incident_agent.workflows._compat import activity_meta, durable_sleep
from incident_agent.workflows.agent_loop import MCP_RETRY, TOOL_RETRY, AgentLoop


@workflow.defn
class IncidentWorkflow:
    @workflow.init
    def __init__(self, inp: IncidentInput) -> None:
        self._inp = inp
        self._state: SessionState = inp.carried_state or SessionState(
            incident_id=inp.incident_id,
            service=inp.service,
            severity=inp.severity,
            scenario=inp.scenario,
            llm_mode=inp.llm_mode,
        )
        self._agent = AgentLoop(self._state)
        self._inbox: list[ChatMessage] = []
        self._decision: Optional[ApprovalDecision] = None
        self._close_requested = False

    # ------------------------------------------------------------------ #
    # Main flow                                                          #
    # ------------------------------------------------------------------ #

    @workflow.run
    async def run(self, inp: IncidentInput) -> IncidentReport:
        st = self._state
        if inp.carried_state is None:
            await self._investigate()

        # The session stays open for follow-up questions. Waiting here costs nothing:
        # no worker thread is held, and the wait survives restarts.
        while True:
            if workflow.info().is_continue_as_new_suggested():
                await workflow.wait_condition(workflow.all_handlers_finished)
                st.continued_runs += 1
                workflow.continue_as_new(dataclasses.replace(inp, carried_state=st))

            try:
                await workflow.wait_condition(
                    lambda: bool(self._inbox) or self._close_requested,
                    timeout=timedelta(seconds=inp.idle_timeout_seconds),
                )
            except asyncio.TimeoutError:
                workflow.logger.info("Incident session idle; closing")
                break

            if not self._inbox:
                break  # close requested and nothing left to answer

            message = self._inbox.pop(0)
            message.turn = st.turn + 1
            st.messages.append(message)
            await self._agent.run_turn(
                purpose="follow_up",
                system_prompt=FOLLOW_UP_SYSTEM.format(
                    incident_id=st.incident_id, service=st.service
                ),
                workflow_tools={"propose_remediation": self._propose_remediation},
            )
            self._record_tokens()

        await workflow.wait_condition(workflow.all_handlers_finished)
        return self._report()

    async def _investigate(self) -> None:
        st, inp = self._state, self._inp
        self._set_phase(Phases.INVESTIGATING)

        ticket: Ticket = await workflow.execute_activity(
            Activities.OPEN_TICKET,
            TicketRequest(
                incident_id=inp.incident_id,
                service=inp.service,
                severity=inp.severity,
                title=inp.alert,
            ),
            result_type=Ticket,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=TOOL_RETRY,
            **activity_meta(f"Open ticket for {inp.service}"),
        )
        st.ticket_id = ticket.ticket_id

        # Tool discovery is non-deterministic (the server could change), so it is an
        # activity. Its result is recorded, so replays always see the same tool list.
        catalog: McpToolCatalog = await workflow.execute_activity(
            Activities.DISCOVER_MCP_TOOLS,
            McpDiscoveryRequest(incident_id=inp.incident_id),
            result_type=McpToolCatalog,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=MCP_RETRY,
            **activity_meta("Discover MCP tools via LangChain MCP adapters"),
        )
        st.tools = list(catalog.tools) + list(NATIVE_TOOLS)

        st.messages.append(
            ChatMessage(
                role="user",
                author="alert",
                turn=st.turn + 1,
                content=(
                    f"ALERT [{inp.severity}] {inp.service}: {inp.alert}\n"
                    f"Incident {inp.incident_id}, ticket {ticket.ticket_id}. "
                    "Investigate and remediate per the runbook."
                ),
            )
        )

        summary = await self._agent.run_turn(
            purpose="investigate",
            system_prompt=INVESTIGATE_SYSTEM.format(
                incident_id=inp.incident_id, service=inp.service, severity=inp.severity
            ),
            workflow_tools={"propose_remediation": self._propose_remediation},
        )
        st.summary = summary

        if st.phase not in (Phases.RESOLVED, Phases.ESCALATED, Phases.FAILED):
            # The agent finished without a verified change (for example, unknown service).
            self._set_phase(Phases.ESCALATED)

        await workflow.execute_activity(
            Activities.UPDATE_TICKET,
            TicketNote(
                ticket_id=ticket.ticket_id,
                note=summary,
                status="resolved" if st.phase == Phases.RESOLVED else "escalated",
            ),
            result_type=str,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=TOOL_RETRY,
            **activity_meta("Post incident summary to ticket"),
        )
        self._record_tokens()

    # ------------------------------------------------------------------ #
    # The approval-gated remediation tool (handled in workflow code)     #
    # ------------------------------------------------------------------ #

    async def _propose_remediation(self, call: ToolCall) -> tuple[str, bool]:
        st, inp = self._state, self._inp
        args = call.args if isinstance(call.args, dict) else {}
        action = str(args.get("action", "")).strip().lower()
        rationale = str(args.get("rationale", "")).strip()
        target_version = args.get("target_version") or None

        if action not in VALID_ACTIONS:
            return f"Invalid action '{action}'. Use one of: {', '.join(sorted(VALID_ACTIONS))}.", True
        if action == "rollback" and not target_version:
            return "A rollback needs target_version (the last known-good version).", True

        requires_approval = action in HIGH_RISK_ACTIONS
        proposal = RemediationProposal(
            action=action,
            rationale=rationale,
            target_version=str(target_version) if target_version else None,
            risk="high" if requires_approval else "low",
            requires_approval=requires_approval,
            proposed_at_turn=st.turn,
        )
        st.proposal = proposal
        workflow.upsert_search_attributes([sa.REMEDIATION_ACTION.value_set(action)])

        if inp.scenario == "workflow_bug":
            # ------------------------------------------------------------------
            # DEMO BUG: comment out the next line, then restart the Python worker.
            # Until then, every workflow task attempt fails here and the workflow
            # waits. No LLM call or tool call that already completed is repeated.
            # ------------------------------------------------------------------
            raise RuntimeError("Simulated bug in change-policy evaluation (workflow_bug scenario)")

        approver = "change-policy (auto)"
        if requires_approval:
            decision = await self._await_approval(proposal)
            if decision is None:
                self._set_phase(Phases.ESCALATED)
                await self._page(
                    f"Approval for {action} on {st.service} timed out. Manual follow-up needed.",
                )
                return (
                    f"No approval arrived within {inp.approval_timeout_seconds}s. The incident "
                    "was escalated to on-call. Do not propose this change again.",
                    False,
                )
            if not decision.approved:
                self._set_phase(Phases.ESCALATED)
                comment = f" Comment: {decision.comment}" if decision.comment else ""
                return (
                    f"Rejected by {decision.reviewer}.{comment} The change was not executed. "
                    "Summarize the situation and recommend next steps.",
                    False,
                )
            approver = decision.reviewer
        else:
            st.approval_status = ApprovalStatuses.AUTO_APPROVED

        self._set_phase(Phases.REMEDIATING)
        attempt_no = len(st.change_results) + 1
        try:
            # Cross-language call: this workflow type is implemented in Java (Spring Boot)
            # and polled on its own task queue. The payload is plain JSON.
            change: ChangeResult = await workflow.execute_child_workflow(
                CHANGE_WORKFLOW_TYPE,
                ChangeRequest(
                    incident_id=st.incident_id,
                    service=st.service,
                    action=action,
                    target_version=proposal.target_version,
                    rationale=rationale,
                    requested_by="incident-copilot",
                    approved_by=approver,
                ),
                id=f"{st.incident_id}-change-{attempt_no}",
                task_queue=CHANGE_TASK_QUEUE,
                result_type=ChangeResult,
                execution_timeout=timedelta(minutes=10),
            )
        except ChildWorkflowError as err:
            root = _root_application_error(err)
            reason = root.message if root else str(err)
            await self._compensate(action, reason)
            st.error = reason
            self._set_phase(Phases.FAILED)
            # Non-retryable business failure: the workflow fails, visibly and on purpose.
            raise ApplicationError(
                f"Change management rejected the {action} for {st.service}: {reason}",
                type="RemediationRejected",
                non_retryable=True,
            ) from err

        st.change_results.append(change)
        self._set_phase(Phases.VERIFYING)
        checks = await self._verify(action)
        healthy = checks[-1].healthy if checks else True
        self._set_phase(Phases.RESOLVED if healthy else Phases.ESCALATED)

        steps = "; ".join(change.steps) or "no steps reported"
        verification = " | ".join(c.summary for c in checks) or "not run"
        return (
            f"Change {change.change_id} {change.status} by the Java change-management "
            f"workflow (approved by {approver}). Steps: {steps}. Verification over "
            f"{len(checks)} checks: {verification}. Service is "
            f"{'healthy' if healthy else 'still degraded'}.",
            False,
        )

    async def _await_approval(self, proposal: RemediationProposal) -> Optional[ApprovalDecision]:
        st, inp = self._state, self._inp
        self._decision = None
        st.awaiting_approval = True
        st.approval_status = ApprovalStatuses.PENDING
        self._set_phase(Phases.AWAITING_APPROVAL)

        target = f" to {proposal.target_version}" if proposal.target_version else ""
        await self._page(f"Approval needed: {proposal.action}{target} on {st.service}. {proposal.rationale}")

        try:
            # Durable wait for a human. Could be seconds or days; costs nothing while waiting.
            await workflow.wait_condition(
                lambda: self._decision is not None,
                timeout=timedelta(seconds=inp.approval_timeout_seconds),
            )
        except asyncio.TimeoutError:
            pass
        finally:
            st.awaiting_approval = False

        decision = self._decision
        st.approval = decision
        if decision is None:
            st.approval_status = ApprovalStatuses.TIMED_OUT
        elif decision.approved:
            st.approval_status = ApprovalStatuses.APPROVED
        else:
            st.approval_status = ApprovalStatuses.REJECTED
        self._set_phase(st.phase)  # refresh ApprovalStatus search attribute
        return decision

    async def _verify(self, action: str) -> list[HealthCheckResult]:
        st, inp = self._state, self._inp
        results: list[HealthCheckResult] = []
        for n in range(1, inp.monitor_checks + 1):
            await durable_sleep(
                inp.monitor_interval_seconds, f"Wait before health check {n}/{inp.monitor_checks}"
            )
            result: HealthCheckResult = await workflow.execute_activity(
                Activities.CHECK_SERVICE_HEALTH,
                HealthCheckRequest(
                    incident_id=st.incident_id,
                    service=st.service,
                    action=action,
                    check_number=n,
                    total_checks=inp.monitor_checks,
                ),
                result_type=HealthCheckResult,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=TOOL_RETRY,
                **activity_meta(f"Health check {n}/{inp.monitor_checks}"),
            )
            results.append(result)
            st.health_checks.append(result)
        return results

    async def _compensate(self, action: str, reason: str) -> None:
        st = self._state
        await workflow.execute_activity(
            Activities.UPDATE_TICKET,
            TicketNote(
                ticket_id=st.ticket_id or "unassigned",
                note=f"Automated {action} was rejected by change management: {reason} Escalating to manual handling.",
                status="escalated",
            ),
            result_type=str,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=TOOL_RETRY,
            **activity_meta("Compensate: mark ticket escalated"),
        )
        await self._page(f"Manual action needed on {st.service}: automated {action} rejected ({reason})")

    async def _page(self, message: str) -> None:
        st = self._state
        await workflow.execute_activity(
            Activities.NOTIFY_ONCALL,
            OncallPage(incident_id=st.incident_id, service=st.service, message=message, urgency="high"),
            result_type=str,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=TOOL_RETRY,
            **activity_meta("Page on-call"),
        )

    # ------------------------------------------------------------------ #
    # Visibility                                                         #
    # ------------------------------------------------------------------ #

    def _set_phase(self, phase: str) -> None:
        self._state.phase = phase
        workflow.upsert_search_attributes(
            [
                sa.AGENT_PHASE.value_set(phase),
                sa.APPROVAL_STATUS.value_set(self._state.approval_status),
            ]
        )

    def _record_tokens(self) -> None:
        st = self._state
        workflow.upsert_search_attributes(
            [sa.LLM_TOKENS.value_set(st.input_tokens + st.output_tokens)]
        )

    def _report(self) -> IncidentReport:
        st = self._state
        last_change = st.change_results[-1] if st.change_results else None
        return IncidentReport(
            incident_id=st.incident_id,
            service=st.service,
            outcome=st.phase,
            ticket_id=st.ticket_id,
            summary=st.summary,
            remediation=st.proposal.action if st.proposal else None,
            change_id=last_change.change_id if last_change else None,
            llm_calls=st.llm_calls,
            tool_calls=st.tool_calls,
            total_tokens=st.input_tokens + st.output_tokens,
        )

    # ------------------------------------------------------------------ #
    # Message handlers                                                   #
    # ------------------------------------------------------------------ #

    @workflow.update
    async def submit_approval(self, decision: ApprovalDecision) -> str:
        """Human-in-the-loop decision. An Update (not a Signal) so the caller gets an
        immediate, validated answer."""
        self._decision = decision
        verdict = "Approved" if decision.approved else "Rejected"
        return f"{verdict} by {decision.reviewer}"

    @submit_approval.validator
    def validate_submit_approval(self, decision: ApprovalDecision) -> None:
        if not self._state.awaiting_approval:
            raise ValueError("No remediation is waiting for approval")
        if not decision.reviewer or not decision.reviewer.strip():
            raise ValueError("A reviewer name is required")

    @workflow.signal
    def send_message(self, text: str) -> None:
        """Follow-up question from an engineer; answered in a new agent turn."""
        text = (text or "").strip()
        if text:
            self._inbox.append(ChatMessage(role="user", content=text, author="on-call"))

    @workflow.signal
    def close_incident(self) -> None:
        self._close_requested = True

    @workflow.query
    def get_state(self) -> SessionState:
        return self._state


def _root_application_error(err: BaseException) -> Optional[ApplicationError]:
    """Walk the failure chain (ChildWorkflowError -> ActivityError -> ApplicationError)
    and return the deepest ApplicationError, which carries the Java-side message."""
    found: Optional[ApplicationError] = None
    current: Optional[BaseException] = err
    depth = 0
    while current is not None and depth < 10:
        if isinstance(current, ApplicationError):
            found = current
        current = getattr(current, "cause", None) or current.__cause__
        depth += 1
    return found
