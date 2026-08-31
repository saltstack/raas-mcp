"""Unit tests for raas_mcp.http_config.load()."""

from __future__ import annotations

import pytest

from raas_mcp.http_config import load

# ---------------------------------------------------------------------------
# a. Defaults from minimal env
# ---------------------------------------------------------------------------

def test_load_minimal_env(monkeypatch):
    """load() with only RAAS_URL set applies all defaults."""
    monkeypatch.setenv("RAAS_URL", "https://raas.example.com")
    for key in ("MCP_PORT", "METRICS_PORT", "TOKEN_TTL_SECONDS", "RAAS_INSECURE",
                "CORS_ALLOWED_ORIGINS", "TLS_ENABLED", "TLS_CERT_PATH", "TLS_KEY_PATH"):
        monkeypatch.delenv(key, raising=False)

    cfg = load()
    assert cfg.mcp_port == 8080
    assert cfg.metrics_port == 9090
    assert cfg.token_ttl_seconds == 3600
    assert cfg.raas_insecure is False
    assert cfg.cors_allowed_origins == []
    assert cfg.tls_enabled is False
    assert cfg.tls_cert_path is None
    assert cfg.tls_key_path is None


# ---------------------------------------------------------------------------
# b. Custom port values
# ---------------------------------------------------------------------------

def test_load_custom_ports(monkeypatch):
    """load() honours MCP_PORT and METRICS_PORT overrides."""
    monkeypatch.setenv("RAAS_URL", "https://raas.example.com")
    monkeypatch.setenv("MCP_PORT", "9000")
    monkeypatch.setenv("METRICS_PORT", "9100")

    cfg = load()
    assert cfg.mcp_port == 9000
    assert cfg.metrics_port == 9100


# ---------------------------------------------------------------------------
# c. CORS origins parsed from comma-separated string
# ---------------------------------------------------------------------------

def test_load_cors_origins(monkeypatch):
    """load() parses CORS_ALLOWED_ORIGINS into a list."""
    monkeypatch.setenv("RAAS_URL", "https://raas.example.com")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://app.example.com, https://ui.example.com")

    cfg = load()
    assert cfg.cors_allowed_origins == ["https://app.example.com", "https://ui.example.com"]


# ---------------------------------------------------------------------------
# d. Missing RAAS_URL raises ValueError
# ---------------------------------------------------------------------------

def test_load_missing_raas_url_raises(monkeypatch):
    """load() raises ValueError when RAAS_URL is absent."""
    monkeypatch.delenv("RAAS_URL", raising=False)
    monkeypatch.delenv("RAASS_URL", raising=False)

    with pytest.raises(ValueError, match="RAAS_URL"):
        load()


# ---------------------------------------------------------------------------
# e. TLS_ENABLED with both paths succeeds
# ---------------------------------------------------------------------------

def test_load_tls_enabled(monkeypatch):
    """load() populates TLS fields when TLS_ENABLED=true."""
    monkeypatch.setenv("RAAS_URL", "https://raas.example.com")
    monkeypatch.setenv("TLS_ENABLED", "true")
    monkeypatch.setenv("TLS_CERT_PATH", "/certs/tls.crt")
    monkeypatch.setenv("TLS_KEY_PATH", "/certs/tls.key")

    cfg = load()
    assert cfg.tls_enabled is True
    assert cfg.tls_cert_path == "/certs/tls.crt"
    assert cfg.tls_key_path == "/certs/tls.key"


# ---------------------------------------------------------------------------
# f. TLS_ENABLED without cert path raises
# ---------------------------------------------------------------------------

def test_load_tls_enabled_missing_cert_raises(monkeypatch):
    """load() raises ValueError when TLS_ENABLED but TLS_CERT_PATH is absent."""
    monkeypatch.setenv("RAAS_URL", "https://raas.example.com")
    monkeypatch.setenv("TLS_ENABLED", "true")
    monkeypatch.delenv("TLS_CERT_PATH", raising=False)
    monkeypatch.setenv("TLS_KEY_PATH", "/certs/tls.key")

    with pytest.raises(ValueError, match="TLS_CERT_PATH"):
        load()


# ---------------------------------------------------------------------------
# g. RAAS_INSECURE flag
# ---------------------------------------------------------------------------

def test_load_raas_insecure(monkeypatch):
    """load() sets raas_insecure=True when RAAS_INSECURE=1."""
    monkeypatch.setenv("RAAS_URL", "https://raas.example.com")
    monkeypatch.setenv("RAAS_INSECURE", "1")

    cfg = load()
    assert cfg.raas_insecure is True


# ---------------------------------------------------------------------------
# h. RAASS_URL fallback
# ---------------------------------------------------------------------------

def test_load_raass_url_fallback(monkeypatch):
    """load() accepts RAASS_URL as an alias for RAAS_URL."""
    monkeypatch.delenv("RAAS_URL", raising=False)
    monkeypatch.setenv("RAASS_URL", "https://raas-alias.example.com")

    cfg = load()
    assert cfg.raas_url == "https://raas-alias.example.com"


# ---------------------------------------------------------------------------
# T045: VIDB configuration tests
# ---------------------------------------------------------------------------

def test_vidb_issuer_url_set(monkeypatch):
    """VIDB_ISSUER_URL env var is parsed into cfg.vidb_issuer_url."""
    monkeypatch.setenv("RAAS_URL", "https://raas.example.com")
    monkeypatch.setenv("VIDB_ISSUER_URL", "https://vidb.test/oidc/t1")
    monkeypatch.delenv("VIDB_JWKS_REFRESH", raising=False)

    cfg = load()
    assert cfg.vidb_issuer_url == "https://vidb.test/oidc/t1"


def test_vidb_issuer_url_absent(monkeypatch):
    """VIDB_ISSUER_URL not set → cfg.vidb_issuer_url is None."""
    monkeypatch.setenv("RAAS_URL", "https://raas.example.com")
    monkeypatch.delenv("VIDB_ISSUER_URL", raising=False)

    cfg = load()
    assert cfg.vidb_issuer_url is None


def test_vidb_issuer_url_empty_string(monkeypatch):
    """VIDB_ISSUER_URL='' (Helm ConfigMap default) → cfg.vidb_issuer_url is None."""
    monkeypatch.setenv("RAAS_URL", "https://raas.example.com")
    monkeypatch.setenv("VIDB_ISSUER_URL", "")

    cfg = load()
    assert cfg.vidb_issuer_url is None


def test_vidb_jwks_refresh_set(monkeypatch):
    """VIDB_JWKS_REFRESH=600 → cfg.vidb_jwks_refresh_interval_seconds == 600."""
    monkeypatch.setenv("RAAS_URL", "https://raas.example.com")
    monkeypatch.setenv("VIDB_JWKS_REFRESH", "600")
    monkeypatch.delenv("VIDB_ISSUER_URL", raising=False)

    cfg = load()
    assert cfg.vidb_jwks_refresh_interval_seconds == 600


def test_vidb_jwks_refresh_below_minimum_raises(monkeypatch):
    """VIDB_JWKS_REFRESH=100 (< 300) → raises ValueError."""
    monkeypatch.setenv("RAAS_URL", "https://raas.example.com")
    monkeypatch.setenv("VIDB_JWKS_REFRESH", "100")

    with pytest.raises(ValueError, match="VIDB_JWKS_REFRESH"):
        load()


def test_vidb_issuer_url_http_non_localhost_raises(monkeypatch):
    """VIDB_ISSUER_URL with plain http (non-localhost) → raises ValueError."""
    monkeypatch.setenv("RAAS_URL", "https://raas.example.com")
    monkeypatch.setenv("VIDB_ISSUER_URL", "http://vidb.example.com/oidc/t1")

    with pytest.raises(ValueError, match="HTTPS"):
        load()


def test_vidb_issuer_url_localhost_http_accepted(monkeypatch):
    """VIDB_ISSUER_URL=http://localhost/... is accepted for dev environments."""
    monkeypatch.setenv("RAAS_URL", "https://raas.example.com")
    monkeypatch.setenv("VIDB_ISSUER_URL", "http://localhost/oidc/t1")

    cfg = load()
    assert cfg.vidb_issuer_url == "http://localhost/oidc/t1"
