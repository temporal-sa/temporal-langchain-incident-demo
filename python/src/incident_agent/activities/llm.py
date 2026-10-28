"""call_llm: where LangChain and the model run.

One activity execution is one model decision. LangChain handles the model
interface (message formats, tool binding, parsing tool calls). Temporal handles
timeouts and retries, so the client's own retries are switched off and errors are
classified as retryable or not.

The provider is configuration, not code:

    LLM_PROVIDER=anthropic   ChatAnthropic (the default)
    LLM_PROVIDER=<other>     LangChain's init_chat_model, for any provider it supports
                             (openai, azure_openai, ollama, bedrock_converse, google_genai, ...)

The model must support tool calling.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Optional

from temporalio import activity
from temporalio.exceptions import ApplicationError

from incident_agent.agent import mock_llm
from incident_agent.agent.langchain_adapter import (
    extract_text,
    extract_tool_calls,
    to_anthropic_tools,
    to_langchain_messages,
    to_openai_tools,
)
from incident_agent.config import Settings, settings
from incident_agent.shared.constants import Activities
from incident_agent.shared.models import LlmRequest, LlmResponse

# Providers whose LangChain classes accept max_retries and timeout.
_RETRY_TIMEOUT_PROVIDERS = {
    "anthropic", "openai", "azure_openai", "google_genai", "mistralai", "groq",
    "fireworks", "together", "deepseek", "xai",
}
# Providers that name the output-token limit differently.
_TOKEN_ARG = {"ollama": "num_predict", "google_genai": "max_output_tokens", "google_vertexai": "max_output_tokens"}


def model_options(s: Settings) -> dict[str, Any]:
    """Keyword arguments for the chat model. Pure function, so it is unit-tested."""
    opts: dict[str, Any] = {_TOKEN_ARG.get(s.llm_provider, "max_tokens"): s.llm_max_tokens}
    if s.llm_provider in _RETRY_TIMEOUT_PROVIDERS:
        opts["max_retries"] = 0  # Temporal owns retries.
        opts["timeout"] = 90
    # Some models only accept their default sampling settings, so temperature is
    # sent only when it is set explicitly.
    if s.llm_temperature is not None:
        opts["temperature"] = s.llm_temperature
    if s.llm_base_url:
        opts["base_url"] = s.llm_base_url
    opts.update(s.llm_kwargs)  # LLM_KWARGS: provider-specific extras, applied last
    return opts


def build_chat_model(s: Settings = settings, *, max_tokens: Optional[int] = None) -> Any:
    opts = model_options(s)
    if max_tokens is not None:
        opts[_TOKEN_ARG.get(s.llm_provider, "max_tokens")] = max_tokens
    if s.llm_provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=s.llm_model, **opts)
    try:
        from langchain.chat_models import init_chat_model
    except ImportError as e:
        raise ApplicationError(
            "LLM_PROVIDER is not anthropic, which needs the 'langchain' package. Run: make setup",
            type="LlmConfigError",
            non_retryable=True,
        ) from e
    try:
        return init_chat_model(s.llm_model, model_provider=s.llm_provider, **opts)
    except ImportError as e:
        raise ApplicationError(
            f"The LangChain package for provider '{s.llm_provider}' isn't installed ({e}). "
            "Run make setup with LLM_PROVIDER set in .env; the README lists the package for each provider.",
            type="LlmConfigError",
            non_retryable=True,
        ) from e


@activity.defn(name=Activities.CALL_LLM)
async def call_llm(req: LlmRequest) -> LlmResponse:
    info = activity.info()
    activity.logger.info(
        "LLM call: purpose=%s turn=%s step=%s attempt=%s mode=%s provider=%s tools=%s",
        req.purpose, req.turn, req.iteration + 1, info.attempt, req.llm_mode, settings.llm_provider, len(req.tools),
    )

    if req.llm_mode == "mock":
        return mock_llm.respond(req)

    missing = settings.llm_missing_env()
    if missing:
        raise ApplicationError(
            f"{', '.join(missing)} not set for LLM_PROVIDER={settings.llm_provider}. "
            "Add it to .env, or start the run in Scripted mode.",
            type="LlmConfigError",
            non_retryable=True,
        )
    if not settings.llm_model:
        raise ApplicationError(
            f"LLM_MODEL is not set for LLM_PROVIDER={settings.llm_provider}.", type="LlmConfigError", non_retryable=True
        )

    try:
        model = build_chat_model()
        tools = to_anthropic_tools(req.tools) if settings.llm_provider == "anthropic" else to_openai_tools(req.tools)
        runnable = model.bind_tools(tools) if req.tools else model
    except ApplicationError:
        raise
    except Exception as e:  # noqa: BLE001 - bad settings won't fix themselves on retry
        raise ApplicationError(
            f"Could not set up {settings.llm_provider} model '{settings.llm_model}': {type(e).__name__}: {e}",
            type="LlmConfigError",
            non_retryable=True,
        ) from e

    try:
        message = await runnable.ainvoke(to_langchain_messages(req.system_prompt, req.messages))
    except Exception as e:  # noqa: BLE001 - classified below
        raise classify_error(e) from e

    usage = getattr(message, "usage_metadata", None) or {}
    metadata = getattr(message, "response_metadata", None) or {}
    return LlmResponse(
        content=extract_text(message.content),
        tool_calls=extract_tool_calls(message),
        model=str(metadata.get("model") or metadata.get("model_name") or metadata.get("model_id") or settings.llm_model),
        input_tokens=int(usage.get("input_tokens", 0) or 0),
        output_tokens=int(usage.get("output_tokens", 0) or 0),
        stop_reason=str(metadata.get("stop_reason") or metadata.get("finish_reason") or metadata.get("done_reason") or ""),
        chat_model=type(model).__name__,
    )


def classify_error(e: Exception) -> ApplicationError:
    """Map any provider's exception onto Temporal's retry semantics.

    Works by HTTP status and exception class name, so it covers the Anthropic and
    OpenAI SDKs (and the many providers built on them) without importing either.
    """
    status = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)
    name = type(e).__name__
    text = f"{name}: {e}"
    if status in (401, 403) or "Authentication" in name or "PermissionDenied" in name:
        return ApplicationError(f"The LLM provider rejected the credentials. {text}", type="LlmAuthError", non_retryable=True)
    if status == 429 or "RateLimit" in name:
        return ApplicationError(f"Rate limited by the LLM provider. {text}", type="LlmRateLimited", next_retry_delay=_retry_after(e))
    if status in (400, 404, 422) or name in ("BadRequestError", "NotFoundError", "UnprocessableEntityError", "ValidationException"):
        return ApplicationError(f"The LLM provider rejected the request. {text}", type="LlmBadRequest", non_retryable=True)
    if isinstance(status, int) and status >= 500:
        return ApplicationError(f"LLM provider error {status}. {text}", type="LlmServerError")
    return ApplicationError(f"Could not complete the LLM call. {text}", type="LlmConnectionError")


def _retry_after(error: Exception) -> Optional[timedelta]:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None) or {}
    value = headers.get("retry-after") if hasattr(headers, "get") else None
    try:
        return timedelta(seconds=float(value)) if value else None
    except (TypeError, ValueError):
        return None
