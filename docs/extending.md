# Extending the demo

## Add an MCP tool

Add a function to `mcp-server/src/ops_mcp/server.py`:

```python
@mcp.tool()
def get_error_budget(service: str) -> str:
    """Remaining monthly error budget for a service."""
    _guard("get_error_budget", service)
    return json.dumps({"service": service, "remaining_pct": 37})
```

Restart the MCP server. New incidents discover the tool automatically. No Python agent change is needed, because discovery happens at run time through `langchain-mcp-adapters`. Running incidents keep the catalog they recorded at their start.

## Build an MCP server on Temporal

The ops-tools MCP server here is deliberately a plain, separate service: it plays the part of "someone else's system". You can also put Temporal behind an MCP server, so Temporal sits on both sides of the protocol:

* **Client side (this repo):** the agent's MCP calls are activities, so a flaky server is just a retry.
* **Server side:** the MCP server's tool handlers are thin. Each one starts a workflow, sends an Update, or queries one. Long-running tools (run diagnostics for ten minutes, provision an environment, wait for a human) become durable: they survive MCP server restarts, get retries and timeouts per step, and show up in the Temporal UI.

For a tool that takes longer than a client will wait, return a handle immediately (`start_diagnostics` returns a workflow ID) and expose a second tool to check on it (`get_diagnostics_status` queries the workflow). For a short tool, the handler can simply await the workflow result.

To add one to this demo: give `mcp-server` a Temporal client and its own worker and task queue, implement `DiagnosticsWorkflow` there, and expose it as an MCP tool. The agent discovers it like any other tool, and the console shows the MCP call as an activity whose service happens to be backed by Temporal.

## Add a tool we own (a Temporal activity)

1. Add a `ToolSpec` with `source="activity"` to `shared/tools.py`, include it in `NATIVE_TOOLS`, and map it in `ACTIVITY_TOOL_ROUTES`.
2. Add the activity name to `shared/constants.py`, implement it in `activities/ops_tools.py`, and register it in `activities/__init__.py`.
3. Build its input in `AgentLoop._dispatch` (the `route` branch).

Because the tool is an activity, it gets retries, timeouts, and an audit trail without any LangChain changes.

## Add a workflow-resident tool

Tools that orchestrate durable primitives (wait for a person, start a child workflow, sleep) belong in workflow code, like `propose_remediation`. Add a `ToolSpec` with `source="workflow"` and pass a handler in the `workflow_tools` dict when calling `AgentLoop.run_turn`. The handler returns `(text_for_the_model, is_error)`.

## Swap the model

The LLM is configuration. Set `LLM_PROVIDER` and `LLM_MODEL` in `.env` (plus that provider's credentials), run `make setup` to install its LangChain package, and restart the worker. The README's *Choosing an LLM* section has settings for OpenAI, Azure OpenAI, OpenAI-compatible servers, Ollama, Bedrock, Gemini, and more.

How it works, in `python/src/incident_agent/activities/llm.py`:

* `build_chat_model()` returns `ChatAnthropic` for Anthropic and calls LangChain's `init_chat_model` for any other provider. Extra keyword arguments come from `LLM_KWARGS`.
* Tool schemas are sent in Anthropic format to Anthropic and in OpenAI function format to everything else, which every LangChain chat model's `bind_tools` accepts.
* `classify_error()` maps any provider's errors onto Temporal retry semantics by HTTP status and exception name.

Workflows store a framework-neutral conversation, so a session already in flight continues with the new model on its next call. The model must support tool calling.

To add a provider that needs custom setup, extend `build_chat_model()`; nothing else changes.

## Serve the console separately from the API

The normal setup runs the console with Vite's dev server, which proxies `/api` to the API on the same origin, so no CORS is involved. To host a built console somewhere else:

1. Build it with the API's address: `cd ui && VITE_API_BASE=https://api.example.com npm run build`, then serve `ui/dist/` from any static host.
2. Let that origin call the API: `CORS_ORIGINS=https://console.example.com` in the API's `.env`.
3. Make the API reachable: `API_HOST=0.0.0.0`. It has no authentication, so put it behind your own authentication or network controls first.

Alternatively, serve `ui/dist/` and proxy `/api` from one origin with any reverse proxy; then you need neither `VITE_API_BASE` nor CORS.

## Add a LangGraph variant side by side

Temporal ships a LangGraph plugin (`temporalio.contrib.langgraph`; Public Preview and `temporalio` 1.27 or later at the time of writing). It runs a LangGraph `StateGraph` as a Temporal workflow, with each node tagged to run as an activity or in the workflow. Building it side by side makes a good comparison:

1. Build a graph with an `agent` node (`execute_in: "activity"`, calls the chat model from `build_chat_model()`) and a `tools` node.
2. Register it: `LangGraphPlugin(graphs={"incident-graph": graph})`, passed to both the client and the worker.
3. Add a `LangGraphIncidentWorkflow` that calls `graph("incident-graph").compile(checkpointer=InMemorySaver()).ainvoke(...)`. Temporal supplies durability, so no external checkpointer is needed.
4. Register it on its own task queue so the existing demo is untouched.

The explicit loop here makes each boundary visible; the plugin version shows the same durability with less glue code.

## Use Nexus for the Java service

The child workflow call works well inside one namespace. When the change-management team owns its own namespace and release cycle, expose `ChangeRequestWorkflow` as a Nexus operation instead. The Python caller then depends on a service contract rather than a workflow type name and task queue.

## Push updates to the console

The console polls once a second. `temporalio.contrib.workflow_streams` (Public Preview) lets a workflow publish durable, offset-addressed events that clients subscribe to. Publish from `AgentLoop` (for example, `turn_started`, `tool_called`) and subscribe from the API with server-sent events.

## AWS Bedrock AgentCore and Strands

Temporal has a durable-execution integration for AWS Strands Agents, which pairs naturally with AgentCore. The boundary in this repo already isolates the agent framework to activities, so a Strands-based turn can replace `call_llm` without touching the outer loop, the approval gate, or the Java hop.

## Hardening for production

* **Versioning.** Use the patching API or Worker Versioning before changing workflow code that running sessions depend on.
* **Payload size.** Every `call_llm` input carries the conversation. For long sessions, store large tool results externally and pass references, or summarize older turns. Continue-as-new is already wired in for history length.
* **Sensitive data.** Prompts and tool results live in history. Add a payload codec to encrypt them, and a codec server for the Temporal UI.
* **Rate limits.** Set worker-level activity concurrency for `call_llm`, or a dedicated task queue with rate limits, so a burst of incidents can't exhaust your model quota.
* **Multi-tenant fairness.** If many customers share one task queue, use task queue fairness keys so one tenant's burst doesn't starve the others.
