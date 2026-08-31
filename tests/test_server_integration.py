"""In-process integration tests for the MCP server.

Uses mcp.client.session.ClientSession with the in-memory transport provided
by anyio memory streams to test the full list_tools / call_tool flow without
starting a real subprocess or connecting to a live RaaS instance.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from mcp import types as mcp_types
from mcp.server import Server
from mcp.shared.memory import create_connected_server_and_client_session


async def _make_server_session(mock_client: MagicMock, minimal_discovery: dict):
    """Build a live in-process MCP server/client pair with mocked dependencies."""
    from raas_mcp import catalog as catalog_mod
    from raas_mcp import dispatcher

    # Rebuild catalog from minimal_discovery
    with patch("raas_mcp.catalog._load_raw_discovery", return_value=minimal_discovery):
        catalog_mod._CATALOG = None
        tool_list = catalog_mod.build_tool_list(allowed=None)
        catalog_entries = catalog_mod.get_catalog_entries(allowed=None)

    server = Server("raas-mcp-server-test")

    @server.list_tools()
    async def _list_tools() -> list[mcp_types.Tool]:
        return tool_list

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict) -> list[mcp_types.TextContent]:
        return await dispatcher.dispatch(
            tool_name=name,
            arguments=arguments or {},
            client=mock_client,
            catalog_entries=catalog_entries,
            approval_gate=[],
            ctx=None,
        )

    return server


class TestListTools:
    @pytest.mark.asyncio
    async def test_list_tools_contains_expected_names(self, mock_api_client, minimal_discovery):
        server = await _make_server_session(mock_api_client, minimal_discovery)

        async with create_connected_server_and_client_session(server) as client_session:
            result = await client_session.list_tools()

        names = {t.name for t in result.tools}
        assert "ret_get_minions" in names
        assert "cmd_route_cmd" in names

    @pytest.mark.asyncio
    async def test_tools_have_descriptions_and_schemas(self, mock_api_client, minimal_discovery):
        server = await _make_server_session(mock_api_client, minimal_discovery)

        async with create_connected_server_and_client_session(server) as client_session:
            result = await client_session.list_tools()

        for tool in result.tools:
            assert tool.description, f"Tool {tool.name} has empty description"
            assert tool.inputSchema is not None, f"Tool {tool.name} has no inputSchema"


class TestCallTool:
    @pytest.mark.asyncio
    async def test_call_tool_success_returns_ok_json(self, mock_api_client, minimal_discovery):
        server = await _make_server_session(mock_api_client, minimal_discovery)

        async with create_connected_server_and_client_session(server) as client_session:
            result = await client_session.call_tool("ret_get_minions", {})

        assert result.content
        body = json.loads(result.content[0].text)
        assert body["ok"] is True
        assert body["resource"] == "ret"
        assert body["method"] == "get_minions"

    @pytest.mark.asyncio
    async def test_call_tool_unknown_param_returns_validation_error(
        self, mock_api_client, minimal_discovery
    ):
        server = await _make_server_session(mock_api_client, minimal_discovery)

        async with create_connected_server_and_client_session(server) as client_session:
            result = await client_session.call_tool(
                "ret_get_minions", {"unknown_param": "x"}
            )

        body = json.loads(result.content[0].text)
        assert body["ok"] is False
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert "unknown_param" in body["error"]["details"]["extra_keys"]

    @pytest.mark.asyncio
    async def test_call_tool_unknown_tool_returns_unknown_tool_error(
        self, mock_api_client, minimal_discovery
    ):
        server = await _make_server_session(mock_api_client, minimal_discovery)

        async with create_connected_server_and_client_session(server) as client_session:
            result = await client_session.call_tool("no_such_tool", {})

        body = json.loads(result.content[0].text)
        assert body["ok"] is False
        assert body["error"]["code"] == "UNKNOWN_TOOL"
