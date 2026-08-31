"""Unit tests for raas_mcp.dispatcher.

Covers:
  - stdio path (shared client)
  - HTTP path (per-request credentials)
  - FR-010: token invalidation on RaaS 401/403
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from raas_mcp.catalog import CatalogEntry
from raas_mcp.dispatcher import dispatch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _entry(resource: str = "ret", method: str = "get_minions",
           required: set | None = None, known: set | None = None) -> CatalogEntry:
    r = frozenset(required or set())
    k = frozenset(known or set())
    return CatalogEntry(
        tool_name=f"{resource}_{method}",
        resource=resource,
        method=method,
        description="test",
        input_schema={"type": "object", "properties": {}, "required": []},
        required_params=r,
        known_params=k,
    )


def _catalog(*entries: CatalogEntry) -> dict[str, CatalogEntry]:
    return {e.tool_name: e for e in entries}


# ---------------------------------------------------------------------------
# a. stdio path: successful dispatch returns success_result
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stdio_path_success():
    mock_client = MagicMock()
    mock_client.api.ret.get_minions.return_value = {"ret": ["m1"]}
    entry = _entry()
    result = await dispatch(
        "ret_get_minions", {},
        client=mock_client,
        catalog_entries=_catalog(entry),
        approval_gate=[],
        ctx=None,
    )
    assert len(result) == 1
    data = json.loads(result[0].text)
    assert data["ok"] is True


# ---------------------------------------------------------------------------
# b. Unknown tool returns UNKNOWN_TOOL error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_tool():
    result = await dispatch(
        "nonexistent_tool", {},
        client=MagicMock(),
        catalog_entries={},
        approval_gate=[],
        ctx=None,
    )
    data = json.loads(result[0].text)
    assert data["ok"] is False
    assert data["error"]["code"] == "UNKNOWN_TOOL"


# ---------------------------------------------------------------------------
# c. Extra parameter key returns VALIDATION_ERROR
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extra_key_returns_validation_error():
    entry = _entry(required=set(), known=set())
    result = await dispatch(
        "ret_get_minions", {"unexpected": "val"},
        client=MagicMock(),
        catalog_entries=_catalog(entry),
        approval_gate=[],
        ctx=None,
    )
    data = json.loads(result[0].text)
    assert data["error"]["code"] == "VALIDATION_ERROR"
    assert "unexpected" in data["error"]["details"]["extra_keys"]


# ---------------------------------------------------------------------------
# d. Missing required parameter returns VALIDATION_ERROR
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_required_key():
    entry = _entry(required={"tgt"}, known={"tgt", "fun"})
    result = await dispatch(
        "ret_get_minions", {"fun": "test.ping"},
        client=MagicMock(),
        catalog_entries=_catalog(entry),
        approval_gate=[],
        ctx=None,
    )
    data = json.loads(result[0].text)
    assert data["error"]["code"] == "VALIDATION_ERROR"
    assert "tgt" in data["error"]["details"]["missing_keys"]


# ---------------------------------------------------------------------------
# e. HTTP path: per-request credentials used to build client
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_http_path_builds_per_request_client():
    entry = _entry()
    mock_per_request_client = MagicMock()
    mock_per_request_client.api.ret.get_minions.return_value = {"ret": ["m1"]}

    with patch(
        "raas_mcp.dispatcher.connect_from_mapping", return_value=mock_per_request_client
    ) as mock_connect:
        result = await dispatch(
            "ret_get_minions", {},
            catalog_entries=_catalog(entry),
            approval_gate=[],
            ctx=None,
            raas_user="alice",
            raas_password="pw",
            raas_url="http://raas.test",
        )

    mock_connect.assert_called_once()
    call_kwargs = mock_connect.call_args[0][0]
    assert call_kwargs["raas"] == "http://raas.test"
    assert "alice" in call_kwargs["auth"]
    data = json.loads(result[0].text)
    assert data["ok"] is True


# ---------------------------------------------------------------------------
# f. FR-010: RaaS 401 during dispatch invalidates the bearer token
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fr010_raas_401_invalidates_token():
    from raas_mcp.auth.token_store import TokenStore

    store = TokenStore(token_ttl_seconds=60)
    token = store.create("alice", "pw")

    entry = _entry()
    mock_client = MagicMock()
    mock_client.api.ret.get_minions.side_effect = Exception("401 Unauthorized")

    result = await dispatch(
        "ret_get_minions", {},
        client=mock_client,
        catalog_entries=_catalog(entry),
        approval_gate=[],
        ctx=None,
        bearer_token=token,
        token_store=store,
    )

    # Token must be invalidated
    assert store.lookup(token) is None
    data = json.loads(result[0].text)
    assert data["ok"] is False


# ---------------------------------------------------------------------------
# g. FR-010: RaaS 403 during dispatch invalidates the bearer token
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fr010_raas_403_invalidates_token():
    from raas_mcp.auth.token_store import TokenStore

    store = TokenStore(token_ttl_seconds=60)
    token = store.create("bob", "pw")

    entry = _entry()
    mock_client = MagicMock()
    mock_client.api.ret.get_minions.side_effect = Exception("403 Forbidden")

    await dispatch(
        "ret_get_minions", {},
        client=mock_client,
        catalog_entries=_catalog(entry),
        approval_gate=[],
        ctx=None,
        bearer_token=token,
        token_store=store,
    )

    assert store.lookup(token) is None


# ---------------------------------------------------------------------------
# h. Non-auth RaaS exception does NOT invalidate the token
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_non_auth_exception_does_not_invalidate_token():
    from raas_mcp.auth.token_store import TokenStore

    store = TokenStore(token_ttl_seconds=60)
    token = store.create("charlie", "pw")

    entry = _entry()
    mock_client = MagicMock()
    mock_client.api.ret.get_minions.side_effect = Exception("500 Internal Server Error")

    await dispatch(
        "ret_get_minions", {},
        client=mock_client,
        catalog_entries=_catalog(entry),
        approval_gate=[],
        ctx=None,
        bearer_token=token,
        token_store=store,
    )

    # Token should remain valid
    assert store.lookup(token) is not None


# ---------------------------------------------------------------------------
# T016: VIDB JWT path tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_vidb_path_builds_bearer_client():
    """vidb_jwt param causes connect_from_mapping to be called with auth_token."""
    entry = _entry()
    mock_per_request_client = MagicMock()
    mock_per_request_client.api.ret.get_minions.return_value = {"ret": ["m1"]}

    with patch(
        "raas_mcp.dispatcher.connect_from_mapping", return_value=mock_per_request_client
    ) as mock_connect:
        result = await dispatch(
            "ret_get_minions", {},
            catalog_entries=_catalog(entry),
            approval_gate=[],
            ctx=None,
            raas_url="http://raas.test",
            vidb_jwt="eyJhbGciOiJSUzI1NiJ9.test.token",
        )

    mock_connect.assert_called_once()
    call_kwargs = mock_connect.call_args[0][0]
    assert call_kwargs.get("auth_token") == "eyJhbGciOiJSUzI1NiJ9.test.token"
    assert "auth" not in call_kwargs  # no Basic Auth
    data = json.loads(result[0].text)
    assert data["ok"] is True


@pytest.mark.asyncio
async def test_vidb_path_raas_401_does_not_invalidate_store():
    """FR-010 VIDB exception: RaaS 401 does NOT call token_store.invalidate()."""
    from raas_mcp.auth.token_store import TokenStore

    store = TokenStore(token_ttl_seconds=60)
    opaque_token = store.create("alice", "pw")

    entry = _entry()
    mock_vidb_client = MagicMock()
    mock_vidb_client.api.ret.get_minions.side_effect = Exception("401 Unauthorized")

    with patch("raas_mcp.dispatcher.connect_from_mapping", return_value=mock_vidb_client):
        result = await dispatch(
            "ret_get_minions", {},
            catalog_entries=_catalog(entry),
            approval_gate=[],
            ctx=None,
            raas_url="http://raas.test",
            bearer_token=opaque_token,
            token_store=store,
            vidb_jwt="some.vidb.jwt",
        )

    # The opaque token should NOT be invalidated (VIDB path skips invalidation)
    assert store.lookup(opaque_token) is not None
    data = json.loads(result[0].text)
    assert data["ok"] is False


@pytest.mark.asyncio
async def test_vidb_path_requires_raas_url():
    """vidb_jwt without raas_url returns RAAS_RPC_ERROR."""
    entry = _entry()
    with patch("raas_mcp.dispatcher.connect_from_mapping", return_value=MagicMock()):
        result = await dispatch(
            "ret_get_minions", {},
            catalog_entries=_catalog(entry),
            approval_gate=[],
            ctx=None,
            vidb_jwt="some.vidb.jwt",
            # raas_url intentionally omitted
        )
    data = json.loads(result[0].text)
    assert data["ok"] is False
    assert data["error"]["code"] == "RAAS_RPC_ERROR"
