"""Unit tests for raas_mcp.auth.token_store."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from raas_mcp.auth.token_store import TokenStore


@pytest.fixture()
def store():
    return TokenStore(token_ttl_seconds=60)


def test_create_returns_64_char_hex(store):
    """create() returns a 64-character lowercase hex string."""
    token = store.create("user1", "pass1")
    assert len(token) == 64
    assert all(c in "0123456789abcdef" for c in token)


def test_lookup_returns_entry_within_ttl(store):
    """lookup() returns the TokenEntry before TTL expires."""
    token = store.create("user1", "pass1")
    entry = store.lookup(token)
    assert entry is not None
    assert entry.raas_user == "user1"
    assert entry.raas_password == "pass1"
    assert entry.token == token


def test_lookup_returns_none_after_ttl_expires(store):
    """lookup() returns None after the TTL has elapsed."""
    with patch("raas_mcp.auth.token_store.time") as mock_time:
        mock_time.time.return_value = 1_000_000.0
        token = store.create("user1", "pass1")
        # Jump past the TTL
        mock_time.time.return_value = 1_000_000.0 + 61.0
        assert store.lookup(token) is None


def test_invalidate_removes_entry(store):
    """invalidate() removes the entry from the store."""
    token = store.create("user1", "pass1")
    store.invalidate(token)
    assert store.lookup(token) is None


def test_lookup_on_unknown_token_returns_none(store):
    """lookup() returns None for a token that was never issued."""
    assert store.lookup("deadbeef" * 8) is None


def test_create_same_user_twice_returns_distinct_tokens(store):
    """Two create() calls for the same user produce distinct tokens."""
    t1 = store.create("user1", "pass1")
    t2 = store.create("user1", "pass1")
    assert t1 != t2
    assert store.lookup(t1) is not None
    assert store.lookup(t2) is not None


def test_expired_entry_evicted_on_lookup(store):
    """After TTL expiry, the internal store no longer holds the entry."""
    with patch("raas_mcp.auth.token_store.time") as mock_time:
        mock_time.time.return_value = 1_000_000.0
        token = store.create("user1", "pass1")
        mock_time.time.return_value = 1_000_000.0 + 61.0
        store.lookup(token)  # triggers lazy eviction
        assert token not in store._store
