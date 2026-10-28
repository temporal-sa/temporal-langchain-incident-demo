"""Provider-neutral LLM configuration and Temporal connection settings (no network needed)."""

import dataclasses

from incident_agent.activities.llm import classify_error, model_options
from incident_agent.agent.langchain_adapter import to_anthropic_tools, to_openai_tools
from incident_agent.config import settings
from incident_agent.shared.models import ToolSpec
from incident_agent.temporal_client import tls_config


def _s(**overrides):
    return dataclasses.replace(settings, **overrides)


# ------------------------------------------------------------------ model options


def test_anthropic_options_match_the_original_setup():
    opts = model_options(_s(llm_provider="anthropic", llm_max_tokens=1500, llm_temperature=None, llm_base_url=None, llm_kwargs={}))
    assert opts == {"max_tokens": 1500, "max_retries": 0, "timeout": 90}


def test_temperature_and_base_url_only_when_set():
    opts = model_options(_s(llm_provider="openai", llm_temperature=0.1, llm_base_url="http://localhost:8001/v1", llm_kwargs={}))
    assert opts["temperature"] == 0.1 and opts["base_url"] == "http://localhost:8001/v1"


def test_provider_specific_token_argument_and_no_retry_args_for_ollama():
    opts = model_options(_s(llm_provider="ollama", llm_max_tokens=800, llm_temperature=None, llm_base_url=None, llm_kwargs={}))
    assert opts == {"num_predict": 800}


def test_llm_kwargs_are_applied_last():
    opts = model_options(_s(llm_provider="azure_openai", llm_temperature=None, llm_base_url=None,
                            llm_kwargs={"azure_deployment": "prod", "timeout": 30}))
    assert opts["azure_deployment"] == "prod" and opts["timeout"] == 30


# ------------------------------------------------------------- error classification


class _Err(Exception):
    def __init__(self, status=None):
        super().__init__("boom")
        self.status_code = status


class AuthenticationError(_Err):
    pass


class RateLimitError(_Err):
    pass


class APIConnectionError(_Err):
    pass


def test_errors_map_to_retry_semantics():
    assert classify_error(AuthenticationError(401)).non_retryable
    assert classify_error(_Err(400)).non_retryable
    assert classify_error(_Err(404)).non_retryable
    assert not classify_error(RateLimitError(429)).non_retryable
    assert not classify_error(_Err(529)).non_retryable
    assert not classify_error(APIConnectionError()).non_retryable
    assert classify_error(RateLimitError(429)).type == "LlmRateLimited"


# ------------------------------------------------------------------- tool schemas


def test_tool_schemas_for_both_formats():
    spec = ToolSpec(name="get_metrics", description="Metrics", input_schema={"type": "object", "properties": {"service": {"type": "string"}}}, source="mcp")
    anthropic = to_anthropic_tools([spec])[0]
    openai = to_openai_tools([spec])[0]
    assert anthropic["input_schema"]["properties"]["service"]["type"] == "string"
    assert openai["type"] == "function" and openai["function"]["name"] == "get_metrics"
    assert openai["function"]["parameters"]["properties"]["service"]["type"] == "string"


# --------------------------------------------------------------------- Temporal TLS


def _t(**overrides):
    base = dict(temporal_api_key=None, temporal_tls=False, temporal_tls_ca=None,
                temporal_tls_cert=None, temporal_tls_key=None, temporal_tls_server_name=None)
    base.update(overrides)
    return _s(**base)


def test_plaintext_by_default_and_tls_when_asked():
    assert tls_config(_t()) is False
    assert tls_config(_t(temporal_api_key="k")) is True
    assert tls_config(_t(temporal_tls=True)) is True


def test_private_ca_and_mtls(tmp_path):
    ca, cert, key = (tmp_path / n for n in ("ca.pem", "client.pem", "client.key"))
    for f in (ca, cert, key):
        f.write_bytes(f.name.encode())
    cfg = tls_config(_t(temporal_tls_ca=str(ca), temporal_tls_cert=str(cert), temporal_tls_key=str(key),
                        temporal_tls_server_name="temporal.internal"))
    assert cfg.server_root_ca_cert == b"ca.pem"
    assert cfg.client_cert == b"client.pem" and cfg.client_private_key == b"client.key"
    assert cfg.domain == "temporal.internal"
