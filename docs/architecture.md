# Architecture

## The one-sentence version

The Temporal workflow is the harness: it owns the session and decides what happens with each answer. LangChain runs one model decision at a time inside an activity. Claude reasons. MCP and Temporal activities do the work.

```text
                      ┌──────────────────── IncidentWorkflow (Python worker) ────────────────────┐
 Console / API ──►    │  outer loop: session state, policy, approvals, timers, visibility         │
 Temporal service     │                                                                           │
                      │   ┌── AgentLoop: one agent turn (inner loop) ──┐                          │
                      │   │  call_llm ──► LangChain ──► the LLM         │                          │
                      │   │  tool calls ──► call_mcp_tool ──► MCP server│                          │
                      │   │             ──► notify_oncall / add_ticket_note                        │
                      │   │             ──► propose_remediation (workflow code)                    │
                      │   └─────────────────────────────────────────────┘                          │
                      │   propose_remediation: policy ─► Update wait for a human ─► child workflow  │
                      └──────────────────────────────────────────────────┬────────────────────────┘
                                                                         ▼
                                                   ChangeRequestWorkflow (Java / Spring Boot worker)
```

## Outer loop vs. inner loop

| Concern | Where it lives | Why |
|---|---|---|
| Conversation and tool results | Workflow state (`SessionState`) | Rebuilt from history on replay; carried across continue-as-new |
| What to do next in a turn | `call_llm` activity (LangChain + the LLM) | Non-deterministic, slow, fallible: an activity |
| Running a tool the model chose | One activity per tool call | Independent retries and timeouts; never re-executed after it completes |
| Discovering MCP tools | `discover_mcp_tools` activity | The catalog can change; recording it keeps replays consistent |
| Change policy, approval, cross-language change | Workflow code (`_propose_remediation`) | It orchestrates durable primitives and can wait for hours |
| Retries | Temporal retry policies | One place to configure; the LLM client has `max_retries=0` |
| Follow-up questions | Signal, then a new agent turn | The session stays open with no thread held |
| Human approval | Update with a validator | The caller gets an immediate, validated answer |
| Visibility | Search attributes upserted as the phase changes | Filter incidents in the Temporal UI or CLI |

The `AgentLoop` class in `workflows/agent_loop.py` is the inner loop's control flow, written as workflow code. That is a deliberate choice for this demo. Each think, act, observe step is a separate event in history, so the console can show the loop step by step, and a crash between any two steps resumes exactly there. LangChain still owns the model interface; the workflow owns the decision of what to do with the answer.

## One agent turn, step by step

1. The workflow appends the user or alert message to `SessionState.messages`.
2. It schedules `call_llm` with the full conversation and the tool specs (MCP tools plus our own).
3. Inside the activity, `langchain_adapter` converts the framework-neutral messages to LangChain messages, the chat model's `bind_tools` binds the tool schemas (`ChatAnthropic` by default, or any provider through `init_chat_model`), and the model is invoked. Provider errors are classified by HTTP status: auth and bad requests are non-retryable; rate limits, 5xx, and connection errors are retryable.
4. The workflow records the assistant message. If there are tool calls, it dispatches them concurrently:
   * MCP tools go to `call_mcp_tool`, which uses `langchain-mcp-adapters`. Unavailability raises (Temporal retries); tool-level answers such as "service not found" come back as an error result the model can read.
   * `notify_oncall` and `add_ticket_note` are ordinary activities.
   * `propose_remediation` is handled in workflow code. It may wait for a human, start the Java child workflow, run durable verification timers, and only then return a result to the model.
5. Tool results are appended, and the loop calls `call_llm` again until the model replies with plain text.

## Cross-language contract

The Python workflow starts `ChangeRequestWorkflow` by name on the `change-management` task queue. Neither codebase imports the other. The contract is:

* workflow type name: `ChangeRequestWorkflow` (the Java interface's simple name),
* task queue: `change-management`,
* payloads: JSON with snake_case field names. Python sends the `ChangeRequest` dataclass; Java deserializes it into a POJO annotated with `@JsonNaming(SnakeCaseStrategy)`. `ChangeResult` comes back the same way.

When Java throws `ApplicationFailure.newNonRetryableFailure(..., "ServiceNotChangeManaged")`, the Java workflow fails, and the Python parent receives a `ChildWorkflowError` whose cause chain ends in an `ApplicationError` carrying the Java message and type. The parent compensates (escalates the ticket, pages on-call) and then fails itself with a non-retryable `RemediationRejected`.

`tests/test_incident_workflow.py` registers a Python stand-in with the same name and task queue, so contract drift breaks the tests.

## How the console builds its graph

The console is not a scripted animation. On each poll (about once a second) the API combines:

1. **Event history** (`fetch_history_events`): activities scheduled, started, completed, and failed with decoded inputs and results; timers; child workflow events; signals; updates; search attribute upserts; workflow task failures.
2. **DescribeWorkflowExecution**: pending activities with their current attempt and last failure (this is how retries show live), plus the pending workflow task's attempt count (how the bug scenario shows as failing).
3. **The `get_state` query**: the conversation, phase, proposal, and counters. A query needs a worker, so when it fails the console says so. That is the worker-crash scenario made visible.

History is flat, so an annotation pass (`api/annotate.py`) recovers the structure the console draws. A `call_llm` result lists the tool calls Claude chose; the activities that follow are matched to those calls by tool name and arguments. Anything else that runs before the next decision belongs to the workflow-resident tool (`propose_remediation`). Each step is tagged with:

* its Temporal primitive (workflow, activity, child workflow, timer, Update, Signal, search attributes, workflow task),
* the standard service an activity calls (the LLM, MCP server, ticketing, paging, monitoring),
* its segment: outer loop, inner loop (agent turn and step), or workflow tool.

The API also fetches each child workflow's own history, so the Java side's activities appear inside the child workflow's box. None of this requires instrumenting the workflows: it is all derived from what Temporal already records.

The two views use the same structure:

* **Layered graph**: People, then Temporal (workflows on the left, activities on the right), then standard services. LangChain appears as a badge on the activities it runs inside.
* **Sequence view**: lifelines for people, the workflow, activities, and services; one row per step; arrows from workflow to activity to service and back. UML-style frames mark each agent turn (`loop`), parallel tool calls (`par`), and workflow code serving a tool call (`tool`).

## Failure semantics

| Failure | Mechanism | Result |
|---|---|---|
| MCP server returns 503 | `call_mcp_tool` raises a retryable `ApplicationError` | Retried with backoff (1s, 1.5x, capped at 4s, 10 attempts) |
| MCP says the service doesn't exist | Returned as an error tool result | The model reads it and stops |
| LLM rate limit or 5xx (any provider) | Retryable `ApplicationError`, honoring `retry-after` | Retried by Temporal |
| Bad API key or invalid request | Non-retryable `ApplicationError` | No retries; the workflow fails with the cause in history |
| Bug in workflow code | Unhandled non-Temporal exception | Workflow task fails and is retried until the code is fixed; completed steps are not re-run |
| Worker process dies | Nothing special | Work pauses; a new worker replays history and continues |
| Java rejects the change | Non-retryable failure from the child workflow | Compensation, then the workflow fails |
| No approval in time | `wait_condition` timeout | Escalation; the model is told not to retry |

## Determinism rules followed here

* Workflow modules import only `temporalio` and the framework-free `shared/` package (through `imports_passed_through`).
* Activities are called by name, so LangChain, LLM provider SDKs, and the MCP client are never imported into the sandbox.
* No environment reads, clocks, or randomness in workflow code. Tool call IDs come from the model's response, which is recorded.
* Optional SDK features (activity and timer summaries) are detected by signature inspection, which is deterministic.

## Where Temporal and LangChain meet

| | LangChain | Temporal |
|---|---|---|
| Scope | One model call or one tool call | The whole session, for as long as it lives |
| Gives you | Model abstraction, message formats, tool binding, MCP adapters | Durable state, retries, timeouts, timers, human waits, cross-language calls, visibility |
| Runs | Inside activities | Around them, in workflow code |
| On failure | Raises | Retries the step, or resumes the session from history |

They don't compete for the same job. LangChain makes one agent step easy to write; Temporal makes the sequence of steps impossible to lose.

## Deliberate choices

* **Explicit loop rather than the LangGraph plugin.** Temporal's LangGraph integration (`temporalio.contrib.langgraph`) is the natural production path for teams already on LangGraph, but at the time of writing it is in Public Preview. The explicit loop uses stable APIs and makes every boundary visible. `docs/extending.md` shows how to add a LangGraph variant side by side.
* **The LLM is configuration.** Only `activities/llm.py` knows which provider is used: `ChatAnthropic` for Anthropic (the default), LangChain's `init_chat_model` for everything else. Tool schemas are converted to each provider's format, errors are classified by HTTP status rather than by SDK, and workflows store a framework-neutral conversation, so switching providers needs no workflow changes, even for sessions already in flight.
* **Polling rather than streaming.** The console polls once a second. `temporalio.contrib.workflow_streams` (Public Preview at the time of writing) could push updates instead.
* **Plain dataclasses on the wire.** Readable JSON in the Temporal UI and painless Java interop. Switch to the Pydantic data converter if you need richer types.
