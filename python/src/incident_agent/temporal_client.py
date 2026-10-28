"""One place to build a Temporal client: local dev server, self-hosted, or Temporal Cloud.

    nothing set                      plaintext (local dev server, or self-hosted without TLS)
    TEMPORAL_API_KEY                 API key over TLS (Temporal Cloud)
    TEMPORAL_TLS_CERT + _KEY         mTLS (Temporal Cloud or self-hosted)
    TEMPORAL_TLS=true                server-side TLS only (self-hosted behind TLS)
    TEMPORAL_TLS_CA                  trust a private CA (self-hosted); combines with any of the above
    TEMPORAL_TLS_SERVER_NAME         override the server name checked against the certificate
"""

from __future__ import annotations

from pathlib import Path

from temporalio.client import Client
from temporalio.service import TLSConfig

from incident_agent.config import Settings, settings as default_settings


def tls_config(settings: Settings) -> TLSConfig | bool:
    wants_tls = bool(
        settings.temporal_api_key
        or settings.temporal_tls
        or settings.temporal_tls_ca
        or (settings.temporal_tls_cert and settings.temporal_tls_key)
    )
    if not wants_tls:
        return False
    custom = settings.temporal_tls_ca or settings.temporal_tls_server_name or settings.temporal_tls_cert
    if not custom:
        return True  # TLS with the system's trusted CAs
    return TLSConfig(
        server_root_ca_cert=Path(settings.temporal_tls_ca).read_bytes() if settings.temporal_tls_ca else None,
        domain=settings.temporal_tls_server_name,
        client_cert=Path(settings.temporal_tls_cert).read_bytes() if settings.temporal_tls_cert and settings.temporal_tls_key else None,
        client_private_key=Path(settings.temporal_tls_key).read_bytes() if settings.temporal_tls_cert and settings.temporal_tls_key else None,
    )


async def connect(settings: Settings = default_settings) -> Client:
    kwargs: dict = {"namespace": settings.temporal_namespace, "tls": tls_config(settings)}
    if settings.temporal_api_key:
        kwargs["api_key"] = settings.temporal_api_key
    return await Client.connect(settings.temporal_address, **kwargs)
