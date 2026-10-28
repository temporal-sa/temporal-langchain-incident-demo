"""Process configuration from environment variables (and the repo-root .env).

Imported by the worker, activities, API, and doctor. Never imported by workflow
code, which must not read the environment.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import find_dotenv, load_dotenv

_ENV_FILE = find_dotenv(usecwd=True)
load_dotenv(_ENV_FILE)
# Relative paths in .env (TLS files) are relative to the repository root, where .env lives.
_ROOT = Path(_ENV_FILE).parent if _ENV_FILE else Path.cwd()

# Display names for providers, and the LangChain class each one uses.
PROVIDER_LABELS = {
    "anthropic": "Claude",
    "openai": "OpenAI",
    "azure_openai": "Azure OpenAI",
    "ollama": "Ollama",
    "bedrock_converse": "Amazon Bedrock",
    "google_genai": "Gemini",
    "google_vertexai": "Vertex AI",
    "mistralai": "Mistral",
    "groq": "Groq",
}
CHAT_MODEL_CLASSES = {
    "anthropic": "ChatAnthropic",
    "openai": "ChatOpenAI",
    "azure_openai": "AzureChatOpenAI",
    "ollama": "ChatOllama",
    "bedrock_converse": "ChatBedrockConverse",
    "google_genai": "ChatGoogleGenerativeAI",
    "google_vertexai": "ChatVertexAI",
    "mistralai": "ChatMistralAI",
    "groq": "ChatGroq",
}
# Credentials each provider needs in the environment (checked before calling it).
REQUIRED_ENV = {
    "anthropic": ["ANTHROPIC_API_KEY"],
    "openai": ["OPENAI_API_KEY"],
    "azure_openai": ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"],
    "google_genai": ["GOOGLE_API_KEY"],
    "mistralai": ["MISTRAL_API_KEY"],
    "groq": ["GROQ_API_KEY"],
}
DEFAULT_MODELS = {"anthropic": "claude-sonnet-5-5"}


def _get(*names: str, default: str | None = None) -> str | None:
    """First non-empty value among several variable names (newer names first)."""
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


def _int(*names: str, default: int) -> int:
    try:
        return int(_get(*names, default=str(default)))
    except ValueError:
        return default


def _float(*names: str) -> float | None:
    try:
        value = _get(*names)
        return float(value) if value else None
    except ValueError:
        return None


def _bool(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def _path(name: str) -> str | None:
    value = _get(name)
    if not value:
        return None
    path = Path(value).expanduser()
    return str(path if path.is_absolute() else (_ROOT / path).resolve())


def _json(name: str) -> dict[str, Any]:
    value = _get(name)
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except ValueError:
        raise SystemExit(f"{name} must be a JSON object, for example {name}='{{\"key\": \"value\"}}'")


@dataclass(frozen=True)
class Settings:
    # Temporal
    temporal_address: str
    temporal_namespace: str
    temporal_api_key: str | None
    temporal_tls: bool
    temporal_tls_ca: str | None
    temporal_tls_cert: str | None
    temporal_tls_key: str | None
    temporal_tls_server_name: str | None
    temporal_ui_url: str
    # LLM
    llm_provider: str
    llm_model: str
    llm_base_url: str | None
    llm_max_tokens: int
    llm_temperature: float | None
    llm_kwargs: dict[str, Any] = field(default_factory=dict)
    default_llm_mode: str = "live"
    # Everything else
    mcp_url: str = "http://localhost:8765/mcp"
    mcp_admin_url: str = "http://localhost:8765/admin"
    data_dir: Path = Path(".demo-data")
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: tuple[str, ...] = ()

    @property
    def llm_label(self) -> str:
        return PROVIDER_LABELS.get(self.llm_provider, self.llm_provider)

    @property
    def chat_model_class(self) -> str:
        return CHAT_MODEL_CLASSES.get(self.llm_provider, "init_chat_model")

    def llm_missing_env(self) -> list[str]:
        return [name for name in REQUIRED_ENV.get(self.llm_provider, []) if not os.getenv(name, "").strip()]

    @classmethod
    def from_env(cls) -> "Settings":
        provider = (_get("LLM_PROVIDER", default="anthropic") or "anthropic").lower()
        mcp_base = (_get("MCP_BASE_URL", default="http://localhost:8765") or "").rstrip("/")
        return cls(
            temporal_address=_get("TEMPORAL_ADDRESS", default="localhost:7233"),
            temporal_namespace=_get("TEMPORAL_NAMESPACE", default="default"),
            temporal_api_key=_get("TEMPORAL_API_KEY"),
            temporal_tls=_bool("TEMPORAL_TLS"),
            temporal_tls_ca=_path("TEMPORAL_TLS_CA"),
            temporal_tls_cert=_path("TEMPORAL_TLS_CERT"),
            temporal_tls_key=_path("TEMPORAL_TLS_KEY"),
            temporal_tls_server_name=_get("TEMPORAL_TLS_SERVER_NAME"),
            temporal_ui_url=(_get("TEMPORAL_UI_URL", default="http://localhost:8233") or "").rstrip("/"),
            llm_provider=provider,
            # ANTHROPIC_* names still work, so existing .env files keep working.
            llm_model=_get("LLM_MODEL", *(["ANTHROPIC_MODEL"] if provider == "anthropic" else []),
                           default=DEFAULT_MODELS.get(provider, "")),
            llm_base_url=_get("LLM_BASE_URL"),
            llm_max_tokens=_int("LLM_MAX_TOKENS", "ANTHROPIC_MAX_TOKENS", default=1500),
            llm_temperature=_float("LLM_TEMPERATURE", "ANTHROPIC_TEMPERATURE"),
            llm_kwargs=_json("LLM_KWARGS"),
            default_llm_mode=_get("LLM_MODE", default="live"),
            mcp_url=f"{mcp_base}/mcp",
            mcp_admin_url=f"{mcp_base}/admin",
            data_dir=Path(_get("DEMO_DATA_DIR", default=".demo-data")).resolve(),
            api_host=_get("API_HOST", default="127.0.0.1"),
            api_port=_int("API_PORT", default=8000),
            cors_origins=tuple(o.strip().rstrip("/") for o in (_get("CORS_ORIGINS") or "").split(",") if o.strip()),
        )


settings = Settings.from_env()
