"""Shared pytest fixtures for raas_mcp unit and integration tests."""

from __future__ import annotations

import socket
from typing import Any
from unittest.mock import MagicMock

import pytest

from raas_mcp.auth.token_store import TokenStore
from raas_mcp.http_config import HttpServerConfig


# ---------------------------------------------------------------------------
# HTTP-mode shared fixtures (used by Phase 2+ tests)
# ---------------------------------------------------------------------------

def _free_port() -> int:
    """Return an ephemeral port that is currently free on 127.0.0.1."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def http_config_fixture(tmp_path):
    """Minimal :class:`HttpServerConfig` using free ephemeral ports."""
    return HttpServerConfig(
        mcp_port=_free_port(),
        metrics_port=_free_port(),
        raas_url="http://raas.test",
        raas_insecure=False,
        token_ttl_seconds=60,
    )


@pytest.fixture()
def token_store_fixture():
    """Fresh :class:`TokenStore` with a 60-second TTL."""
    return TokenStore(token_ttl_seconds=60)


@pytest.fixture()
def mock_raas_client_fixture():
    """MagicMock RaaS API client whose ``api.api.get_versions()`` returns a valid stub."""
    client = MagicMock()
    client.api.api.get_versions.return_value = {"ret": {"raas": "8.18.4"}}
    client.api.ret.get_minions.return_value = {"ret": ["minion-01"]}
    client.api.cmd.route_cmd.return_value = {"ret": {"cmd_id": "test-123"}}
    return client


@pytest.fixture()
def mock_api_client() -> MagicMock:
    """MagicMock of sseapiclient.apiclient.APIClient.

    Provides a nested ``.api.ret.get_minions()`` that returns a minimal
    successful RaaS payload, and ``.api.cmd.route_cmd()`` for dispatcher tests.
    """
    client = MagicMock()
    client.api.ret.get_minions.return_value = {"ret": ["minion-01", "minion-02"]}
    client.api.cmd.route_cmd.return_value = {"ret": {"cmd_id": "abc-123"}}
    return client


@pytest.fixture()
def vidb_config_fixture():
    """Minimal :class:`HttpServerConfig` with VIDB issuer URL configured."""
    return HttpServerConfig(
        mcp_port=_free_port(),
        metrics_port=_free_port(),
        raas_url="http://raas.test",
        raas_insecure=False,
        token_ttl_seconds=60,
        vidb_issuer_url="https://vidb.test/oidc/tenant",
        vidb_jwks_refresh_interval_seconds=300,
    )


@pytest.fixture()
def minimal_discovery() -> dict[str, Any]:
    """Minimal api_discovery.json structure with two resources for unit tests.

    Uses the same nested ``{resource: {method: minfo}}`` format as the real
    catalog so catalog.py and dispatcher.py tests don't need to load the full
    ~750 KB file.
    """
    return {
        "ret": {
            "__doc__": "Return data resource.",
            "get_minions": {
                "formatted": "Get list of accepted minions.",
                "detailed": {
                    "doc": "Return the list of accepted minion IDs.",
                    "signature": "get_minions()",
                    "returns": "list",
                    "schema": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": False,
                    },
                },
            },
        },
        "cmd": {
            "__doc__": "Command execution resource.",
            "route_cmd": {
                "formatted": "Route a command to one or more minions.",
                "detailed": {
                    "doc": "Route a Salt command to the specified targets.",
                    "signature": "route_cmd(tgt, fun, arg=None)",
                    "returns": "dict",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "tgt": {"type": "string"},
                            "fun": {"type": "string"},
                            "arg": {"type": "array"},
                        },
                        "required": ["tgt", "fun"],
                        "additionalProperties": False,
                    },
                },
            },
        },
    }
