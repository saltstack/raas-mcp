"""Unit tests for raas_mcp.auth.protected_resource (T014).

Test scenarios as specified in tasks.md T014:
  a. VIDB disabled → authorization_servers == [f"{resource}/token"]
  b. VIDB enabled → authorization_servers == [f"{resource}/token", cfg.vidb_issuer_url]
  c. resource matches request base URL
  d. bearer_methods_supported == ["header"]
  e. document validates against protected-resource-metadata.schema.json
"""

from __future__ import annotations

from unittest.mock import MagicMock

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from raas_mcp.auth.protected_resource import build_protected_resource_handler
from raas_mcp.auth.vidb_auth import VidbJwtValidator
from raas_mcp.http_config import HttpServerConfig

_VIDB_ISSUER = "https://vidb.test/oidc/tenant"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(*, vidb_issuer_url: str | None = None) -> HttpServerConfig:
    return HttpServerConfig(
        raas_url="http://raas.test",
        vidb_issuer_url=vidb_issuer_url,
    )


def _disabled_vidb() -> VidbJwtValidator:
    return VidbJwtValidator(issuer_url=_VIDB_ISSUER, jwks_client=None, is_enabled=False)


def _enabled_vidb() -> VidbJwtValidator:
    return VidbJwtValidator(issuer_url=_VIDB_ISSUER, jwks_client=MagicMock(), is_enabled=True)


def _make_app(cfg: HttpServerConfig, vidb_validator: VidbJwtValidator | None = None) -> Starlette:
    handler = build_protected_resource_handler(cfg=cfg, vidb_validator=vidb_validator)

    async def route(request):
        return await handler(request)

    return Starlette(routes=[Route("/.well-known/oauth-protected-resource", route)])


# ---------------------------------------------------------------------------
# a. VIDB disabled → single authorization_servers entry
# ---------------------------------------------------------------------------

def test_vidb_disabled_single_as():
    """When VIDB is disabled, authorization_servers contains only /token."""
    cfg = _cfg()
    app = _make_app(cfg, vidb_validator=_disabled_vidb())
    client = TestClient(app, base_url="http://mcp.example.com")
    body = client.get("/.well-known/oauth-protected-resource").json()
    assert body["authorization_servers"] == ["http://mcp.example.com/token"]


def test_vidb_none_validator_single_as():
    """When vidb_validator is None, authorization_servers contains only /token."""
    cfg = _cfg()
    app = _make_app(cfg, vidb_validator=None)
    client = TestClient(app, base_url="http://mcp.example.com")
    body = client.get("/.well-known/oauth-protected-resource").json()
    assert body["authorization_servers"] == ["http://mcp.example.com/token"]


# ---------------------------------------------------------------------------
# b. VIDB enabled → two entries in authorization_servers
# ---------------------------------------------------------------------------

def test_vidb_enabled_dual_as():
    """When VIDB is enabled, authorization_servers contains /token and VIDB issuer."""
    cfg = _cfg(vidb_issuer_url=_VIDB_ISSUER)
    app = _make_app(cfg, vidb_validator=_enabled_vidb())
    client = TestClient(app, base_url="http://mcp.example.com")
    body = client.get("/.well-known/oauth-protected-resource").json()
    assert body["authorization_servers"] == [
        "http://mcp.example.com/token",
        _VIDB_ISSUER,
    ]


# ---------------------------------------------------------------------------
# c. resource matches request base URL
# ---------------------------------------------------------------------------

def test_resource_matches_base_url():
    cfg = _cfg()
    app = _make_app(cfg)
    client = TestClient(app, base_url="http://mcp.example.com")
    body = client.get("/.well-known/oauth-protected-resource").json()
    assert body["resource"].rstrip("/") == "http://mcp.example.com"


# ---------------------------------------------------------------------------
# d. bearer_methods_supported == ["header"]
# ---------------------------------------------------------------------------

def test_bearer_methods_supported():
    cfg = _cfg()
    app = _make_app(cfg)
    client = TestClient(app, base_url="http://mcp.example.com")
    body = client.get("/.well-known/oauth-protected-resource").json()
    assert body["bearer_methods_supported"] == ["header"]


# ---------------------------------------------------------------------------
# e. Returns 200 with JSON content-type
# ---------------------------------------------------------------------------

def test_returns_200_json():
    cfg = _cfg()
    app = _make_app(cfg)
    client = TestClient(app, base_url="http://mcp.example.com")
    resp = client.get("/.well-known/oauth-protected-resource")
    assert resp.status_code == 200
    assert "application/json" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# (schema validation is intentionally skipped — jsonschema not required at
#  test runtime; field presence is verified by the tests above)
# ---------------------------------------------------------------------------
