"""HTTP-mode configuration for the RaaS MCP server.

Reads settings from environment variables with sensible defaults.  All server
startup validation is done in ``load()``.

Environment variables
---------------------
RAAS_URL                        RaaS base URL (required)
RAAS_INSECURE                   "true"/"1" to skip TLS verification (default: false)
RAAS_TIMEOUT                    float seconds for RaaS HTTP calls (default: 60.0)
MCP_PORT                        port for the MCP/HTTP endpoint (default: 8080)
METRICS_PORT                    port for the Prometheus /metrics endpoint (default: 9090)
TOKEN_TTL_SECONDS               Bearer token lifetime in seconds (default: 3600)
CORS_ALLOWED_ORIGINS            comma-separated list of allowed CORS origins
KEEPALIVE_INTERVAL              SSE keepalive interval in seconds (default: 15)
PRESTOP_DRAIN_SECONDS           grace period on SIGTERM before shutdown (default: 15)
MAX_RAAS_TIMEOUT_SECONDS        cap on long-running RaaS calls (default: 60)
TLS_ENABLED                     "true"/"1" to enable direct TLS (default: false)
TLS_CERT_PATH                   path to PEM certificate for direct TLS
TLS_KEY_PATH                    path to PEM private key for direct TLS
VIDB_ISSUER_URL                 VIDB OIDC issuer URL for SSO JWT validation
                                (empty string or absent = VIDB path disabled)
VIDB_JWKS_REFRESH               JWKS cache TTL in seconds (default: 43200 = 12 h)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class HttpServerConfig:
    # Network
    mcp_port: int = 8080
    metrics_port: int = 9090
    raas_url: str = ""
    raas_insecure: bool = False
    raas_timeout: float = 60.0

    # Auth
    token_ttl_seconds: int = 3600

    # CORS
    cors_allowed_origins: list[str] = field(default_factory=list)

    # SSE keepalive
    keepalive_interval_seconds: int = 15

    # Graceful drain
    prestop_drain_seconds: int = 15
    max_raas_timeout_seconds: int = 60

    # Tool filtering (inherited from server_config)
    allowed_tools: list[str] | None = None
    approval_gate: list[str] = field(default_factory=list)

    # Direct TLS opt-in (FR-003; Ingress TLS is the default)
    tls_enabled: bool = False
    tls_cert_path: str | None = None
    tls_key_path: str | None = None

    # OIDC / VIDB JWT SSO authentication (optional — disabled when issuer URL absent)
    vidb_issuer_url: str | None = None
    vidb_jwks_refresh_interval_seconds: int = 43200


def _bool_env(key: str, default: bool = False) -> bool:
    val = os.environ.get(key, "").strip().lower()
    if not val:
        return default
    return val in ("true", "1", "yes")


def _int_env(key: str, default: int) -> int:
    val = os.environ.get(key, "").strip()
    if not val:
        return default
    return int(val)


def _float_env(key: str, default: float) -> float:
    val = os.environ.get(key, "").strip()
    if not val:
        return default
    return float(val)


def _str_env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _list_env(key: str) -> list[str]:
    val = os.environ.get(key, "").strip()
    if not val:
        return []
    return [s.strip() for s in val.split(",") if s.strip()]


def load() -> HttpServerConfig:
    """Load HTTP server configuration from environment variables."""
    raas_url = _str_env("RAAS_URL") or _str_env("RAASS_URL")
    if not raas_url:
        raise ValueError(
            "RAAS_URL environment variable is required for HTTP transport mode"
        )

    cors_origins = _list_env("CORS_ALLOWED_ORIGINS")

    allowed_tools_raw = _str_env("ALLOWED_TOOLS")
    allowed_tools: list[str] | None = (
        [s.strip() for s in allowed_tools_raw.split(",") if s.strip()]
        if allowed_tools_raw
        else None
    )

    approval_gate_raw = _str_env("APPROVAL_GATE")
    approval_gate: list[str] = (
        [s.strip() for s in approval_gate_raw.split(",") if s.strip()]
        if approval_gate_raw
        else []
    )

    tls_enabled = _bool_env("TLS_ENABLED", False)
    tls_cert_path = _str_env("TLS_CERT_PATH") or None
    tls_key_path = _str_env("TLS_KEY_PATH") or None

    if tls_enabled:
        if not tls_cert_path:
            raise ValueError("TLS_CERT_PATH must be set when TLS_ENABLED=true")
        if not tls_key_path:
            raise ValueError("TLS_KEY_PATH must be set when TLS_ENABLED=true")

    # VIDB / OIDC SSO configuration (optional)
    # Treat empty string (rendered by Helm ConfigMap when not configured) as None.
    vidb_issuer_url: str | None = _str_env("VIDB_ISSUER_URL") or None
    vidb_jwks_refresh = _int_env("VIDB_JWKS_REFRESH", 43200)

    if vidb_issuer_url is not None:
        # Must be HTTPS, or http://localhost for dev
        is_https = vidb_issuer_url.startswith("https://")
        is_localhost_http = vidb_issuer_url.startswith("http://localhost")
        if not (is_https or is_localhost_http):
            raise ValueError(
                f"VIDB_ISSUER_URL must be an HTTPS URI (or http://localhost for dev); "
                f"got: {vidb_issuer_url!r}"
            )
    if vidb_jwks_refresh < 300 or vidb_jwks_refresh > 86400:
        raise ValueError(
            f"VIDB_JWKS_REFRESH must be between 300 and 86400 seconds; "
            f"got: {vidb_jwks_refresh}"
        )

    return HttpServerConfig(
        mcp_port=_int_env("MCP_PORT", 8080),
        metrics_port=_int_env("METRICS_PORT", 9090),
        raas_url=raas_url,
        raas_insecure=_bool_env("RAAS_INSECURE", False),
        raas_timeout=_float_env("RAAS_TIMEOUT", 60.0),
        token_ttl_seconds=_int_env("TOKEN_TTL_SECONDS", 3600),
        cors_allowed_origins=cors_origins,
        keepalive_interval_seconds=_int_env("KEEPALIVE_INTERVAL", 15),
        prestop_drain_seconds=_int_env("PRESTOP_DRAIN_SECONDS", 15),
        max_raas_timeout_seconds=_int_env("MAX_RAAS_TIMEOUT_SECONDS", 60),
        allowed_tools=allowed_tools,
        approval_gate=approval_gate,
        tls_enabled=tls_enabled,
        tls_cert_path=tls_cert_path,
        tls_key_path=tls_key_path,
        vidb_issuer_url=vidb_issuer_url,
        vidb_jwks_refresh_interval_seconds=vidb_jwks_refresh,
    )
