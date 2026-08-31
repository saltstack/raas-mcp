"""Integration tests for the HTTP transport layer (T022).

Uses httpx.ASGITransport to exercise the full Starlette app without a real
network socket.  All RaaS API calls are mocked.

Test scenarios (a–h) as specified in tasks.md:
  a. Unauthenticated POST /mcp → 401
  b. POST /token with valid creds → 200 + access_token
  c. POST /token with invalid creds → 401
  d. Authenticated POST /mcp (initialize) → 200 (session manager running)
  e. Authenticated POST /mcp (tools/list) → 200
  f. POST /token with no Authorization header → 400
  g. GET /.well-known/oauth-protected-resource → 200 with token_endpoint
  h. GET /health/ready → 200; GET /health/live → 200
  -- Multi-user: US5 two users each get distinct tokens and isolated sessions
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import time as _time
from unittest.mock import patch

import httpx
import jwt as _jwt
import pytest
from cryptography.hazmat.backends import default_backend as _default_backend
from cryptography.hazmat.primitives.asymmetric import rsa as _rsa

from raas_mcp.auth.token_store import TokenStore
from raas_mcp.http_config import HttpServerConfig
from raas_mcp.server_http import build_http_app

# ---------------------------------------------------------------------------
# Lifespan helper
# ---------------------------------------------------------------------------

@contextlib.asynccontextmanager
async def _lifespan(app):
    """Trigger ASGI lifespan.startup/shutdown around an async context."""
    startup_complete = asyncio.Event()
    shutdown_request = asyncio.Event()

    async def _receive():
        if not startup_complete.is_set():
            return {"type": "lifespan.startup"}
        await shutdown_request.wait()
        return {"type": "lifespan.shutdown"}

    async def _send(message):
        if message["type"] == "lifespan.startup.complete":
            startup_complete.set()

    scope = {"type": "lifespan", "asgi": {"version": "3.0"}}
    task = asyncio.create_task(app(scope, _receive, _send))
    await startup_complete.wait()
    try:
        yield
    finally:
        shutdown_request.set()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (TimeoutError, asyncio.CancelledError, Exception):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def cfg():
    return HttpServerConfig(
        mcp_port=8080,
        metrics_port=9090,
        raas_url="http://raas.test",
        raas_insecure=False,
        token_ttl_seconds=60,
    )


@pytest.fixture()
def store():
    return TokenStore(token_ttl_seconds=60)


@pytest.fixture()
def http_app(cfg, store):
    app, _mgr = build_http_app(cfg, store)
    return app, store, cfg


def _basic(user: str, pw: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()


# ---------------------------------------------------------------------------
# a. Unauthenticated POST /mcp → 401
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unauthenticated_mcp_returns_401(http_app):
    app, _store, _cfg = http_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/mcp", json={"jsonrpc": "2.0", "method": "initialize", "id": 1, "params": {}}
        )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# b. POST /token with valid creds → 200 + access_token
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_post_token_valid_creds(http_app):
    app, _store, _cfg = http_app
    with patch("raas_mcp.auth.token_endpoint._validate_raas_credentials", return_value=True):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/token", headers={"Authorization": _basic("alice", "pw")})
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"


# ---------------------------------------------------------------------------
# c. POST /token with invalid creds → 401
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_post_token_invalid_creds(http_app):
    app, _store, _cfg = http_app
    with patch("raas_mcp.auth.token_endpoint._validate_raas_credentials", return_value=False):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/token", headers={"Authorization": _basic("alice", "bad")})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# d. Authenticated POST /mcp (initialize) → 200  [needs lifespan]
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_authenticated_mcp_initialize(http_app):
    app, store, _cfg = http_app
    token = store.create("alice", "pw")
    async with _lifespan(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "method": "initialize",
                    "id": 1,
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "test-client", "version": "0.0.1"},
                    },
                },
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json, text/event-stream",
                },
            )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# e. Authenticated POST /mcp (tools/list) → 200  [needs lifespan]
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_authenticated_tools_list(http_app):
    app, store, _cfg = http_app
    token = store.create("alice", "pw")
    async with _lifespan(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "method": "tools/list", "id": 2, "params": {}},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json, text/event-stream",
                },
            )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# f. POST /token with no Authorization header → 400
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_post_token_no_auth_header(http_app):
    app, _store, _cfg = http_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/token")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# g. GET /.well-known/oauth-protected-resource → 200 with authorization_servers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_protected_resource_metadata(http_app):
    app, _store, _cfg = http_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/.well-known/oauth-protected-resource")
    assert resp.status_code == 200
    body = resp.json()
    # RFC 9728: field is authorization_servers (array), not token_endpoint
    assert "authorization_servers" in body
    assert isinstance(body["authorization_servers"], list)
    assert len(body["authorization_servers"]) >= 1


# ---------------------------------------------------------------------------
# h. GET /health/ready and /health/live → 200
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_endpoints(http_app):
    app, _store, _cfg = http_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r1 = await client.get("/health/ready")
        r2 = await client.get("/health/live")
    assert r1.status_code == 200
    assert r2.status_code == 200


# ---------------------------------------------------------------------------
# Multi-user (US5): two users each get distinct tokens
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multi_user_distinct_tokens(http_app):
    app, store, _cfg = http_app
    with patch("raas_mcp.auth.token_endpoint._validate_raas_credentials", return_value=True):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            r1 = await client.post("/token", headers={"Authorization": _basic("alice", "pw1")})
            r2 = await client.post("/token", headers={"Authorization": _basic("bob", "pw2")})

    t1 = r1.json()["access_token"]
    t2 = r2.json()["access_token"]
    assert t1 != t2

    e1 = store.lookup(t1)
    e2 = store.lookup(t2)
    assert e1 is not None and e1.raas_user == "alice"
    assert e2 is not None and e2.raas_user == "bob"


# ---------------------------------------------------------------------------
# T022 VIDB scenarios (i–l)
# ---------------------------------------------------------------------------

_VIDB_ISSUER_URL = "https://vidb.test/oidc/tenant"

# Generate a fresh key pair at module load — avoids hardcoded PEM bytes.
_VIDB_PRIVATE_KEY = _rsa.generate_private_key(
    public_exponent=65537, key_size=2048, backend=_default_backend()
)
_VIDB_PUBLIC_KEY = _VIDB_PRIVATE_KEY.public_key()


def _vidb_token(*, exp_offset: int = 3600, ovl: bool = False) -> str:
    now = int(_time.time())
    payload = {
        "iss": _VIDB_ISSUER_URL,
        "sub": "user@vsphere.local",
        "iat": now,
        "exp": now + exp_offset,
    }
    if ovl:
        payload["ovl"] = True
    else:
        payload["authorization_details"] = [{"type": "vcf_salt_operations"}]
    return _jwt.encode(payload, _VIDB_PRIVATE_KEY, algorithm="RS256")


def _build_vidb_app(store: TokenStore) -> tuple:
    """Build an ASGI app with a pre-wired VidbJwtValidator (real key, no network)."""
    from unittest.mock import MagicMock

    from jwt import PyJWKClient

    from raas_mcp.auth.vidb_auth import VidbJwtValidator
    from raas_mcp.http_config import HttpServerConfig

    signing_key_mock = MagicMock()
    signing_key_mock.key = _VIDB_PUBLIC_KEY

    jwks_client = MagicMock(spec=PyJWKClient)
    jwks_client.get_signing_key_from_jwt.return_value = signing_key_mock

    vidb_validator = VidbJwtValidator(
        issuer_url=_VIDB_ISSUER_URL,
        jwks_client=jwks_client,
        is_enabled=True,
    )

    vidb_cfg = HttpServerConfig(
        mcp_port=8080,
        metrics_port=9090,
        raas_url="http://raas.test",
        raas_insecure=False,
        token_ttl_seconds=60,
        vidb_issuer_url=_VIDB_ISSUER_URL,
    )
    app, _mgr = build_http_app(vidb_cfg, store, vidb_validator)
    return app, store, vidb_cfg, vidb_validator


# ---------------------------------------------------------------------------
# i. VIDB path: POST /mcp with valid VIDB JWT → MCP initialize succeeds
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_vidb_mcp_initialize_valid_jwt():
    """A valid VIDB JWT Bearer token at POST /mcp is accepted (T022i)."""
    store = TokenStore(token_ttl_seconds=60)
    app, _store, _cfg, _v = _build_vidb_app(store)
    token = _vidb_token()
    async with _lifespan(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "method": "initialize",
                    "id": 1,
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "test-vidb", "version": "0.0.1"},
                    },
                },
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json, text/event-stream",
                },
            )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# j. VIDB path: POST /mcp with expired VIDB JWT → 401
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_vidb_mcp_expired_jwt_returns_401():
    """An expired VIDB JWT Bearer token returns 401 (T022j)."""
    store = TokenStore(token_ttl_seconds=60)
    app, _store, _cfg, _v = _build_vidb_app(store)
    expired_token = _vidb_token(exp_offset=-100)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "initialize", "id": 1, "params": {}},
            headers={"Authorization": f"Bearer {expired_token}"},
        )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# k. VIDB dual-AS: GET /.well-known/oauth-protected-resource with VIDB enabled
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_vidb_dual_as_protected_resource():
    """When VIDB is enabled, authorization_servers has two entries (T022k)."""
    store = TokenStore(token_ttl_seconds=60)
    app, _store, _cfg, _v = _build_vidb_app(store)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/.well-known/oauth-protected-resource")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["authorization_servers"]) == 2
    assert _VIDB_ISSUER_URL in body["authorization_servers"]


@pytest.mark.asyncio
async def test_vidb_disabled_single_as_protected_resource(http_app):
    """When VIDB is disabled, authorization_servers has one entry (T022k)."""
    app, _store, _cfg = http_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/.well-known/oauth-protected-resource")
    body = resp.json()
    assert len(body["authorization_servers"]) == 1


# ---------------------------------------------------------------------------
# l. VIDB overflow token: ovl claim present → accepted at MCP layer
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_vidb_overflow_token_accepted():
    """Overflow VIDB JWT (ovl=True, no authorization_details) is accepted (T022l)."""
    store = TokenStore(token_ttl_seconds=60)
    app, _store, _cfg, _v = _build_vidb_app(store)
    overflow_token = _vidb_token(ovl=True)
    async with _lifespan(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "method": "initialize",
                    "id": 1,
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "test-overflow", "version": "0.0.1"},
                    },
                },
                headers={
                    "Authorization": f"Bearer {overflow_token}",
                    "Accept": "application/json, text/event-stream",
                },
            )
    assert resp.status_code == 200
