# Incident copilot demo: Temporal + LangChain + Claude + MCP + Java.
# Run each long-lived target in its own terminal, in this order:
#   make temporal   make mcp   make java   make worker   make api   make ui
# Then: make doctor
# Or start everything from one terminal: make up

TEMPORAL_DB ?= .temporal/dev.db

# The LangChain provider package to install, from LLM_PROVIDER in .env (override: make setup PY_EXTRAS=openai)
LLM_PROVIDER := $(shell sed -n 's/^LLM_PROVIDER=//p' .env 2>/dev/null | tail -n 1 | tr -d '"'"'"' ')
EXTRA_openai := openai
EXTRA_azure_openai := openai
EXTRA_ollama := ollama
EXTRA_bedrock_converse := bedrock
EXTRA_bedrock := bedrock
EXTRA_google_genai := google
EXTRA_mistralai := mistral
EXTRA_groq := groq
PY_EXTRAS ?= $(EXTRA_$(LLM_PROVIDER))
SEARCH_ATTRIBUTES = \
	--search-attribute IncidentService=Keyword \
	--search-attribute IncidentSeverity=Keyword \
	--search-attribute DemoScenario=Keyword \
	--search-attribute AgentPhase=Keyword \
	--search-attribute ApprovalStatus=Keyword \
	--search-attribute RemediationAction=Keyword \
	--search-attribute LlmTokens=Int

.DEFAULT_GOAL := help
.PHONY: up up-demo help setup setup-python setup-mcp setup-ui setup-java env \
        temporal temporal-fresh mcp worker api java ui doctor doctor-live \
        test journal faults reset-faults clean

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-16s %s\n", $$1, $$2}'

up: ## Start everything from one terminal (no tmux); Ctrl-C stops it all
	@scripts/start-all.sh

up-demo: ## Start everything except the Python worker (run `make worker` in its own terminal for the crash scenario)
	@scripts/start-all.sh --skip worker

setup: env setup-python setup-mcp setup-ui setup-java ## Install everything

env: ## Create .env from the example if missing
	@test -f .env || (cp .env.example .env && echo "Created .env; add ANTHROPIC_API_KEY")

setup-python: ## Python agent: uv sync (plus the LangChain package for LLM_PROVIDER)
	cd python && uv sync $(addprefix --extra ,$(PY_EXTRAS))

setup-mcp: ## MCP server: uv sync
	cd mcp-server && uv sync

setup-ui: ## Console: npm install
	cd ui && npm install

setup-java: ## Java worker: compile with Maven
	cd java && mvn -q -DskipTests package

temporal: ## Temporal dev server with search attributes (UI on :8233)
	@mkdir -p .temporal
	temporal server start-dev --db-filename $(TEMPORAL_DB) $(SEARCH_ATTRIBUTES)

temporal-fresh: ## Delete the dev server database, then start it
	rm -f $(TEMPORAL_DB)
	$(MAKE) temporal

mcp: ## MCP ops-tools server on :8765
	cd mcp-server && uv run ops-mcp

java: ## Java / Spring Boot change-management worker (:8081); reads .env like the Python side
	@set -a; [ -f .env ] && . ./.env; set +a; \
	for v in TEMPORAL_TLS_CA TEMPORAL_TLS_CERT TEMPORAL_TLS_KEY; do \
	  eval "p=\$${$$v:-}"; case "$$p" in ""|/*) ;; *) export $$v="$$(pwd)/$$p" ;; esac; \
	done; \
	profile=""; \
	if [ -n "$${TEMPORAL_TLS_CA:-}" ]; then profile=""; \
	elif [ -n "$${TEMPORAL_API_KEY:-}" ]; then profile=apikey; \
	elif [ -n "$${TEMPORAL_TLS_CERT:-}" ] && [ -n "$${TEMPORAL_TLS_KEY:-}" ]; then profile=mtls; fi; \
	cd java && SPRING_PROFILES_ACTIVE="$$profile" mvn spring-boot:run

worker: ## Python worker (agent workflows + LangChain activities)
	cd python && uv run incident-worker

api: ## Demo console API on :8000
	cd python && uv run incident-api

ui: ## Demo console on :5173
	cd ui && npm run dev

doctor: ## Preflight checks; registers missing search attributes
	cd python && uv run incident-doctor --register-search-attributes

doctor-live: ## Preflight checks plus one real call to your LLM
	cd python && uv run incident-doctor --register-search-attributes --live-llm

test: ## Python tests (downloads a Temporal dev server on first run)
	cd python && uv run pytest -q

journal: ## Watch side effects (tickets, pages) as they happen
	@mkdir -p python/.demo-data && touch python/.demo-data/side-effects.jsonl
	tail -f python/.demo-data/side-effects.jsonl

faults: ## Show armed MCP faults and recent MCP calls
	@curl -s localhost:8765/admin/faults; echo

reset-faults: ## Clear armed MCP faults
	@curl -s -X DELETE localhost:8765/admin/faults; echo

clean: ## Remove build output and local demo data
	rm -rf ui/dist java/target python/.demo-data python/.pytest_cache .logs
	find . -name "__pycache__" -type d -prune -exec rm -rf {} +
