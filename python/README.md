# python/: the agent

The Python half of the demo: the agent workflows, the activities where LangChain and the LLM run, and the API behind the console. One [uv](https://docs.astral.sh/uv/) project, `incident-agent`.

```bash
uv sync                 # install (make setup does this, plus the package for LLM_PROVIDER)
uv sync --extra openai  # or install a provider's LangChain package yourself: openai, ollama, bedrock, google, mistral, groq
uv run incident-worker  # Python worker: workflows + activities, task queue "incident-agent"
uv run incident-api     # console API on 127.0.0.1:8000
uv run incident-doctor  # preflight checks (add --register-search-attributes, --live-llm)
uv run pytest -q        # tests
```

Configuration comes from the repository-root `.env` (see the main README).

## Where things are

| Path | What's there |
|---|---|
| `src/incident_agent/temporal_client.py` | Connects to the local dev server, a self-hosted service (plaintext, TLS, private CA, mTLS), or Temporal Cloud |
| `src/incident_agent/workflows/incident.py` | `IncidentWorkflow`: the outer loop. Approval Update, follow-up Signal, state Query, the Java child workflow, verification timers. Also the `DEMO BUG` line |
| `src/incident_agent/workflows/agent_loop.py` | `AgentLoop`: one agent turn, each decision and tool call as an activity. Retry policies live here |
| `src/incident_agent/workflows/triage.py` | `TriageWorkflow`: the short, read-only workflow |
| `src/incident_agent/activities/llm.py` | `call_llm`: builds the LangChain chat model from `LLM_PROVIDER` (`ChatAnthropic` or `init_chat_model`), binds tools, and classifies provider errors as retryable or not |
| `src/incident_agent/activities/mcp_tools.py` | MCP discovery and calls through `langchain-mcp-adapters` |
| `src/incident_agent/activities/ops_tools.py` | Ticketing, paging, and health-check activities (simulated), with a side-effects journal |
| `src/incident_agent/agent/` | The LangChain message adapter and the Scripted model |
| `src/incident_agent/shared/` | Framework-free contracts shared by workflows, activities, the API, and Java: dataclasses, prompts, tool specs, constants, search attributes |
| `src/incident_agent/api/` | FastAPI app, scenario presets, and the code that turns event history into the console's views |
| `tests/` | Unit tests, and workflow tests against a local dev server (with a Python stand-in for the Java workflow) |

## Rules this code follows

- Workflow modules import only `temporalio` and `shared/`. LangChain, LLM provider SDKs, and the MCP client are only ever imported by activities.
- Workflows call activities by name, so the workflow sandbox never loads them.
- Workflow code reads no environment variables, clocks, or random numbers.
