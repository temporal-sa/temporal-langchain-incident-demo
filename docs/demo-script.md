# Guided tour

This tour walks through every scenario in the demo. It says *Claude* because that's the default LLM; if you've configured another provider, the console shows that provider's name instead. Each part says what to do, what to look for, and what it shows about Temporal and its relationship with LangChain. Doing all of it takes about 45 minutes. Each part stands on its own, so feel free to skip around.

## Before you start

1. **Start the demo.** In one terminal:

   ```bash
   make up-demo
   ```

   In a second terminal, start the Python worker on its own:

   ```bash
   make worker
   ```

   Running the worker separately lets you stop and restart it in parts 5 and 7 without touching anything else. (If you'd rather not, `make up` starts everything in one terminal.)

2. **Open two browser tabs:** the console at <http://localhost:5173> and the Temporal UI at <http://localhost:8233>.

3. **Pick a model** at the top of the console's left rail.
   - Start with **Scripted**. It's deterministic, free, and needs no API key, and everything except the model call is real.
   - Once you know what to expect, switch to your live model (the button shows its name: **Claude** by default) and repeat any part. A live model chooses its own tools and words, so runs differ slightly, and that's fine: Temporal records whatever it decided.

4. **Optional:** in a third terminal, run `make journal` to watch side effects (tickets, pages, health checks) as they happen.

### Five ideas to keep in mind

- **Workflow.** Code that orchestrates. Temporal records each thing it does in an **event history**, and can rebuild the workflow's state at any time by replaying that history. Workflow code must be deterministic, so it doesn't talk to the outside world directly.
- **Activity.** Code that does the work: calling Claude, calling an MCP tool, opening a ticket. Activities can fail, so Temporal gives each one timeouts and a retry policy, and records its result. A completed activity is never run again.
- **Outer loop.** The `IncidentWorkflow`. It owns the whole session: the conversation, policy, approvals, timers, and the call into Java.
- **Inner loop.** One agent turn: Claude decides, the tools it chose run, the results go back to Claude, until it replies. Each decision and each tool call is its own activity.
- **LangChain's role.** It runs *inside* activities. In `call_llm` it formats the conversation, binds the tool schemas, calls Claude, and parses the tool calls. In the MCP activities it discovers and calls the MCP server's tools. Temporal wraps all of it.

### Finding your way around the console

- **Left rail:** model choice, scenarios, a short workflow, visibility queries, and recent runs.
- **Architecture graph:** three regions.
  - **People** on the left.
  - **Temporal** in the middle, with workflows on its left and activities on its right.
  - **Standard services** on the right.

  Green **LangChain** badges mark the activities LangChain runs inside. Things light up as they run.
- **Execution, step by step:** a sequence diagram of the run, drawn from the event history. Each row is one event, with arrows from the workflow to an activity, the activity to its service, and back. The frames show structure:
  - **`loop`** is one agent turn.
  - **`par`** marks tool calls that ran in parallel.
  - **`tool`** is workflow code serving a tool call.

  Select a row to see exactly what Temporal recorded.
- **Session:** the conversation the workflow holds. You can ask follow-up questions here.

## 1. Happy path (5 minutes)

**Try it.** Click **Run** next to **Happy path**. A memory leak on checkout-api.

**Watch for**, in order:

1. **`open_ticket`** and **`discover_mcp_tools`** run first, in the outer loop. Tool discovery is an activity: the MCP server's tool list could change at any time, so Temporal records the list this run saw, and every replay sees the same one.
2. A green **`loop`** frame opens: **Inner loop, agent turn 1**. Its first row is **`call_llm`**, with the line `ChatAnthropic.bind_tools(…).ainvoke(…)` (or your provider's chat model). That's LangChain doing its job inside a Temporal activity. Its result reads **Claude chose get_service_info, get_metrics, search_logs**.
3. A **`par`** bracket groups those three tool calls. They run in parallel, each as its own activity, each calling the ops-tools MCP server.
4. More decisions follow (**step 2**, **step 3**), until Claude calls **`propose_remediation`**.
5. A blue **`tool`** frame opens: workflow code takes over from Claude. A restart is low risk, so it's approved automatically, and the **ChangeRequestWorkflow** box appears. That workflow is written in Java and runs on a separate Spring Boot worker. Inside its box, the same pattern repeats: a workflow calling activities that call a service (the CMDB).
6. **Durable timer** rows alternate with **`check_service_health`** activities. That's the verification window. While a timer runs, no thread is held anywhere.
7. Claude's final reply, an incident summary, appears in the **Session** panel. The session stays open for follow-up questions.

**Look closer.** In the Temporal UI, open the workflow (or click **Open in Temporal UI** in the console). Find an `ActivityTaskScheduled` event for `call_llm` and expand its input: the full conversation and tool schemas sent to Claude. The matching `ActivityTaskCompleted` event holds Claude's decision. Everything the agent knows is there.

**What it shows.** LangChain handles one model call at a time. Temporal turns the sequence of calls into a durable session: each step has its own retries, and the whole thing is recorded and inspectable.

## 2. Advanced Visibility (3 minutes)

**Try it.** In the console's left rail, under **Advanced visibility**, click **Rollbacks** or **SEV1 incidents**. Each link opens a filtered list in the Temporal UI. Or use the CLI:

```bash
temporal workflow list --query 'IncidentService = "checkout-api"'
temporal workflow list --query 'AgentPhase = "Resolved" AND LlmTokens > 1000'
```

**Watch for.** In the step-by-step view, the small **Search attributes** rows show the workflow updating `AgentPhase`, `ApprovalStatus`, `RemediationAction`, and `LlmTokens` as it moves.

**What it shows.** Operations teams can find every incident in a given state, such as waiting for approval or using a lot of tokens, without building their own tracking database.

## 3. Human approval (5 minutes)

**Try it.** Run **Human approval**. Errors on payments-api started right after deploy 2.14.0, so the agent proposes a **rollback to 2.13.4**.

**Watch for.**

- Inside the `tool` frame: a page to on-call, then a running **Approval deadline, 15m** timer.
- In the graph, the on-call engineer pulses.
- The status row shows **Review approval**; click it to jump to the approval card.
- The **Waiting for approval** visibility link now finds this run.

The workflow is paused on a durable wait. It could wait like this for days. Restart the worker now if you like: it keeps waiting.

**Approve it**, either in the console or from the CLI:

```bash
temporal workflow update execute --workflow-id <id> --name submit_approval \
  --input '{"approved": true, "reviewer": "alice", "comment": "Rollback approved"}'
```

Then run the same command again. It's rejected with "No remediation is waiting for approval": the Update's validator refuses decisions when nothing is pending, before anything is written to history.

**Variation.** Run the scenario again and click **Reject** with a comment. The agent reads the rejection, doesn't retry, and writes an escalation summary instead.

**What it shows.** Human-in-the-loop is just a durable wait for an Update. There's no polling and no state to persist yourself, and the person gets an immediate, validated answer.

## 4. API downtime (4 minutes)

**Try it.** Run **API downtime**. The console arms a fault in the MCP server: its `get_metrics` tool fails four times, then works.

**Watch for.**

- The `call_mcp_tool` row for `get_metrics` turns orange, with attempt pips: **attempt 2 of 10**, then 3, then 4.
- The service next to it turns red: **failing; Temporal retries**.
- In the graph, the MCP server is red (it's the thing failing) while its activity is orange (Temporal retrying it).
- On attempt 5 it succeeds, and the agent carries on as if nothing happened.
- `make faults` shows the simulated 503s.

**Try a real outage.** Start any scenario, stop the MCP server (Ctrl-C in its terminal, or `kill` it), and start it again with `make mcp`. Same behavior.

**What it shows.** Retries are policy, configured once in the workflow (`MCP_RETRY` in `workflows/agent_loop.py`). LangChain's own retries are switched off, so there's one place to reason about them. Claude never sees the outage. And if the worker crashed mid-outage, the retry schedule would survive it.

## 5. Bug in workflow code (5 minutes)

**Try it.** Run **Bug in workflow code**. A thread pool is saturated on search-api, and the agent investigates normally.

**Watch for.** When Claude calls `propose_remediation`, the workflow hits a deliberate bug.

- The workflow box in the graph turns red: **Workflow task failing, attempt N**.
- The notice line explains what's happening.
- The Python worker's terminal prints the stack trace on each attempt.

The workflow hasn't failed and nothing is lost. Temporal keeps retrying the workflow task until the code is fixed.

**Fix it.**

1. Open `python/src/incident_agent/workflows/incident.py` and find the `DEMO BUG` comment.
2. Comment out the `raise RuntimeError(...)` line under it.
3. Stop the worker (Ctrl-C in its terminal), then `make worker`.
4. On the next retry, the workflow continues. That's usually a few seconds; retries back off, so a longer fix means a slightly longer wait.

**Look closer.** The Claude calls above the bug weren't repeated. The new worker replayed the history, rebuilt the conversation from the recorded results, and carried on. The context the agent built up survived a code bug and a redeploy.

**Afterwards,** restore the bug so the scenario works next time: `git checkout python/src/incident_agent/workflows/incident.py`, then restart the worker.

**What it shows.** Bugs in orchestration code don't destroy in-flight work. Fix and redeploy, and running sessions pick up where they stopped.

## 6. Unrecoverable failure (4 minutes)

**Try it.** Run **Unrecoverable failure**. A memory leak in legacy-billing.

**Watch for.**

- The agent proposes a restart (low risk, approved automatically).
- The Java **ChangeRequestWorkflow** starts, and its first activity, **Validate change against the CMDB**, is **rejected**: legacy-billing isn't registered for automated change management. Run `curl localhost:8081/services` to see what the stand-in CMDB allows.
- Back in Python, the workflow **compensates**: it marks the ticket as escalated and pages on-call. Watch `make journal`.
- Then the workflow **fails**, with a `RemediationRejected` error that carries the Java message.

**What it shows.** Some failures shouldn't be retried. Java marks this one non-retryable, that intent crosses the language boundary intact, and the Python side cleans up before failing visibly. It's the "invalid account" case from a money-transfer saga, in an agent.

## 7. Long-running session and a worker crash (6 minutes)

**Try it.** Run **Long-running session**. A slow memory leak on orders-api, with a verification window of 12 health checks, 10 seconds apart.

1. While a **Wait 10s before a health check** timer row is running, stop the Python worker: Ctrl-C in its terminal, or `kill -9` its process.
2. **Watch for** what happens next.
   - The notice line says **No worker is answering**, and the workflow box in the graph goes dashed.
   - In the Temporal UI, the workflow is still **Running**, with its timer pending.
   - The session lives in Temporal's history, not in the worker process.
3. Start the worker again: `make worker`. The workflow carries on where it was, and the remaining checks run.
4. When it resolves, ask a follow-up in the **Session** panel, such as *What did you change, and why?* The agent answers from the full session, which survived the crash. (In Scripted mode the answer is a summary built from the record; with Claude it's a real answer.)
5. Check `make journal`: one ticket and one set of pages. Nothing was done twice.

If you stop the worker during an activity rather than a timer, that activity is retried once its timeout passes (30 seconds for health checks).

**What it shows.** This is durable execution. The agent's state isn't in memory that can be lost; it's in an event history that any worker can resume from.

## 8. A short workflow (2 minutes)

**Try it.** Under **Short workflow** in the left rail, pick a service and click **Run triage**.

**Watch for.** It finishes in seconds, with a severity, a likely cause, and a next step. It uses the same inner loop and the same activities as the incident workflow, but a different outer loop: read-only tools, a small step budget, and no human gate.

**What it shows.** Short and long workflows are the same programming model. The difference is what the outer loop does.

## 9. Bonus: change your mind mid-session (with a live model)

**Try it.** After a **Happy path** or **Human approval** run resolves, ask the agent in the Session panel to do something new, for example *Please scale out checkout-api as well*.

**Watch for.** A new **`loop`** frame (**agent turn 2, follow-up question**). Scaling out is high risk, so the approval card appears again. It's the same session, hours later if you like, with all its context.

**What it shows.** A session is a long-lived conversation with durable state. Follow-ups are Signals that start new agent turns.

## Where to go next

Each of these is a small change. [docs/extending.md](extending.md) has details.

- **Add an MCP tool** to `mcp-server/src/ops_mcp/server.py`, restart the MCP server, and start a new incident. The agent discovers the tool with no Python changes.
- **Change the retry policy** (`MCP_RETRY` in `python/src/incident_agent/workflows/agent_loop.py`), restart the worker, and rerun **API downtime**.
- **Edit the runbook** in `python/src/incident_agent/shared/prompts.py` and see how Claude's choices change.
- **Try another LLM** by setting `LLM_PROVIDER` and `LLM_MODEL` in `.env`, then `make setup` (see *Choosing an LLM* in the README). Run the same scenarios and compare the choices it makes.
- **Run it on Temporal Cloud** (see the README's Running options).
- **Read [docs/architecture.md](architecture.md)** for why the agent loop is written explicitly, how the Python-to-Java contract works, and the full failure semantics.

## Cleaning up

- **Close open sessions** in the console asks every running session to finish.
- To terminate everything that's still running:

  ```bash
  temporal workflow terminate --query 'WorkflowType = "IncidentWorkflow" AND ExecutionStatus = "Running"' --reason cleanup --yes
  ```

- To start over with an empty history, stop everything, then `make temporal-fresh`.
- To stop the demo, press Ctrl-C in the `make up-demo` terminal and in the worker's terminal.

## Appendix: presenting this demo

If you're showing it to others:

- **Rehearse in Scripted mode.** It's deterministic. If the model API is slow during a live session, switch to Scripted; everything else stays real, and you can say so.
- **Keep the worker in its own terminal** (`make up-demo`, then `make worker`). The crash in part 7 is then a single Ctrl-C.
- **Run `make doctor-live` beforehand.** It confirms every component, including one real call to your LLM.
- **The page never scrolls on its own.** The step-by-step and session panels follow their newest entry only while you're at their bottom. Untick **Follow new steps** to freeze the step-by-step panel while you talk, and use **Review approval** to jump to a pending approval.
- **A good order:** parts 1, 2, 3, 4, 5, 6, 7, then 8. That's about 35 minutes with discussion.
- **A one-breath summary of the graph:** "Standard services on the right. Temporal in the middle: workflows orchestrate, activities do the work. LangChain lives inside the activities. The outer loop owns the session; the inner loop is Claude deciding and Temporal executing."
