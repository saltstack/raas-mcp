"""Unit tests for raas_mcp.auth.token_endpoint."""

from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from raas_mcp.auth.token_endpoint import build_token_handler
from raas_mcp.auth.token_store import TokenStore


def _make_app(store: TokenStore, raas_valid: bool) -> Starlette:
    """Build a minimal Starlette test app with POST /token."""
    handler = build_token_handler(
        store,
        raas_url="http://raas.test",
        raas_insecure=False,
    )

    async def token_route(request):
        return await handler(request)

    app = Starlette(routes=[Route("/token", token_route, methods=["POST"])])
    return app


def _basic_header(user: str, password: str) -> str:
    encoded = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {encoded}"


# -----------------------------------------------------------------------
# a. Missing Authorization header → 400
# -----------------------------------------------------------------------

def test_missing_auth_header_returns_400():
    store = TokenStore(token_ttl_seconds=60)
    with patch("raas_mcp.auth.token_endpoint._validate_raas_credentials", return_value=True):
        app = _make_app(store, raas_valid=True)
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.post("/token")
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_request"


# -----------------------------------------------------------------------
# b. Invalid credentials → 401
# -----------------------------------------------------------------------

def test_invalid_credentials_returns_401():
    store = TokenStore(token_ttl_seconds=60)
    with patch("raas_mcp.auth.token_endpoint._validate_raas_credentials", return_value=False):
        app = _make_app(store, raas_valid=False)
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.post("/token", headers={"Authorization": _basic_header("user", "bad")})
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_client"


# -----------------------------------------------------------------------
# c. Valid credentials → 200 with access_token
# -----------------------------------------------------------------------

def test_valid_credentials_returns_token():
    store = TokenStore(token_ttl_seconds=60)
    with patch("raas_mcp.auth.token_endpoint._validate_raas_credentials", return_value=True):
        app = _make_app(store, raas_valid=True)
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.post("/token", headers={"Authorization": _basic_header("alice", "pw")})
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"
    assert isinstance(body["expires_in"], int)


# -----------------------------------------------------------------------
# d. Returned token is stored and resolvable
# -----------------------------------------------------------------------

def test_returned_token_resolvable_in_store():
    store = TokenStore(token_ttl_seconds=60)
    with patch("raas_mcp.auth.token_endpoint._validate_raas_credentials", return_value=True):
        app = _make_app(store, raas_valid=True)
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.post("/token", headers={"Authorization": _basic_header("alice", "pw")})
    token = resp.json()["access_token"]
    entry = store.lookup(token)
    assert entry is not None
    assert entry.raas_user == "alice"


# -----------------------------------------------------------------------
# e. Non-Basic Authorization scheme → 400
# -----------------------------------------------------------------------

def test_non_basic_scheme_returns_400():
    store = TokenStore(token_ttl_seconds=60)
    with patch("raas_mcp.auth.token_endpoint._validate_raas_credentials", return_value=True):
        app = _make_app(store, raas_valid=True)
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.post("/token", headers={"Authorization": "Bearer some-token"})
    assert resp.status_code == 400


# -----------------------------------------------------------------------
# f. Two requests for same user produce distinct tokens
# -----------------------------------------------------------------------

def test_two_requests_produce_distinct_tokens():
    store = TokenStore(token_ttl_seconds=60)
    with patch("raas_mcp.auth.token_endpoint._validate_raas_credentials", return_value=True):
        app = _make_app(store, raas_valid=True)
        client = TestClient(app, raise_server_exceptions=True)
        r1 = client.post("/token", headers={"Authorization": _basic_header("alice", "pw")})
        r2 = client.post("/token", headers={"Authorization": _basic_header("alice", "pw")})
    assert r1.json()["access_token"] != r2.json()["access_token"]
