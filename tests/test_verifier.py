"""Unit tests for raas_mcp.auth.verifier (T007 + VIDB extension).

Covers:
  a. Valid opaque token returns AccessToken with correct client_id
  b. Expired opaque token returns None
  c. Unknown opaque token returns None
  d. AccessToken.scopes == ["raas"]
  e. VIDB validator None (_vidb_validator=None) → routing always goes to opaque TokenStore
  f. VIDB validator present but _is_enabled=False → routing still falls through to TokenStore
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from raas_mcp.auth.token_store import TokenStore
from raas_mcp.auth.verifier import DualModeTokenVerifier
from raas_mcp.auth.vidb_auth import VidbJwtValidator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def store_and_verifier():
    store = TokenStore(token_ttl_seconds=60)
    verifier = DualModeTokenVerifier(store)
    return store, verifier


# ---------------------------------------------------------------------------
# a. Valid opaque token returns AccessToken with correct client_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_valid_token_returns_access_token(store_and_verifier):
    store, verifier = store_and_verifier
    token = store.create("alice", "secret")
    result = await verifier.verify_token(token)
    assert result is not None
    assert result.client_id == "alice"
    assert result.token == token
    assert "raas" in result.scopes


# ---------------------------------------------------------------------------
# b. Expired opaque token returns None
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_expired_token_returns_none():
    store = TokenStore(token_ttl_seconds=1)
    verifier = DualModeTokenVerifier(store)
    token = store.create("bob", "pw")

    # Force expiry by manipulating the entry's expires_at
    entry = store.lookup(token)
    assert entry is not None
    entry.expires_at = time.time() - 1  # expired

    result = await verifier.verify_token(token)
    assert result is None


# ---------------------------------------------------------------------------
# c. Unknown opaque token returns None
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_token_returns_none(store_and_verifier):
    _, verifier = store_and_verifier
    assert await verifier.verify_token("deadbeef" * 8) is None


# ---------------------------------------------------------------------------
# d. AccessToken.scopes == ["raas"]
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_access_token_scopes(store_and_verifier):
    store, verifier = store_and_verifier
    token = store.create("carol", "pw")
    result = await verifier.verify_token(token)
    assert result is not None
    assert result.scopes == ["raas"]


# ---------------------------------------------------------------------------
# e. VIDB validator=None → always uses opaque path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_vidb_none_always_uses_opaque_path():
    store = TokenStore(token_ttl_seconds=60)
    verifier = DualModeTokenVerifier(store, vidb_validator=None)
    token = store.create("dave", "pw")
    result = await verifier.verify_token(token)
    assert result is not None
    assert result.client_id == "dave"


# ---------------------------------------------------------------------------
# f. VIDB validator present but _is_enabled=False → falls through to TokenStore
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_vidb_disabled_falls_through_to_opaque():
    store = TokenStore(token_ttl_seconds=60)
    disabled_vidb = VidbJwtValidator(
        issuer_url="https://vidb.test/oidc/t1",
        jwks_client=None,
        is_enabled=False,
    )
    verifier = DualModeTokenVerifier(store, vidb_validator=disabled_vidb)
    token = store.create("eve", "pw")
    result = await verifier.verify_token(token)
    assert result is not None
    assert result.client_id == "eve"


# ---------------------------------------------------------------------------
# g. AccessToken.expires_at is a future integer timestamp
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_access_token_has_expires_at(store_and_verifier):
    store, verifier = store_and_verifier
    token = store.create("charlie", "pw")
    result = await verifier.verify_token(token)
    assert result is not None
    assert result.expires_at is not None
    assert result.expires_at > int(time.time())


# ---------------------------------------------------------------------------
# h. Invalidated token returns None
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invalidated_token_returns_none(store_and_verifier):
    store, verifier = store_and_verifier
    token = store.create("frank", "pw")
    store.invalidate(token)
    assert await verifier.verify_token(token) is None
