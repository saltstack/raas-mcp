"""MCP server entry point for raas-mcp-server.

Starts a stdio-transport MCP server that exposes every RaaS API
resource/method pair as an MCP tool.  Credentials and configuration are
read from ``~/.salt/config.yml`` (or ``RAAS_MCP_CONFIG`` to override the
path) — see ``raas_mcp/config_file.py``.
"""

from __future__ import annotations

import asyncio
import sys

from mcp import types as mcp_types
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server

from raas_mcp import catalog, dispatcher, server_config


async def run() -> None:
    """Build catalog, connect to RaaS, register handlers, and serve stdio."""
    cfg = server_config.load()

    try:
        from raas_mcp.raas_client import connect_from_mapping
        raas_client = connect_from_mapping(
            {
                "raas": cfg.raas_url,
                "auth": cfg.auth,
                "config_name": cfg.config_name,
                "timeout": cfg.timeout,
                "insecure": cfg.insecure,
            }
        )
    except SystemExit:
        raise
    except Exception as exc:
        print(f"raas-mcp-server: failed to build RaaS client: {exc}", file=sys.stderr)
        sys.exit(1)

    tool_list = catalog.build_tool_list(allowed=cfg.allowed_tools)
    catalog_entries = catalog.get_catalog_entries(allowed=cfg.allowed_tools)

    async def _handle_list_tools(
        ctx: ServerRequestContext, params: mcp_types.PaginatedRequestParams | None
    ) -> mcp_types.ListToolsResult:
        return mcp_types.ListToolsResult(tools=tool_list)

    async def _handle_call_tool(
        ctx: ServerRequestContext, params: mcp_types.CallToolRequestParams
    ) -> mcp_types.CallToolResult:
        content = await dispatcher.dispatch(
            tool_name=params.name,
            arguments=params.arguments or {},
            client=raas_client,
            catalog_entries=catalog_entries,
            approval_gate=cfg.approval_gate,
            ctx=None,
        )
        return mcp_types.CallToolResult(content=content)

    server = Server(
        "raas-mcp-server",
        on_list_tools=_handle_list_tools,
        on_call_tool=_handle_call_tool,
    )

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> None:
    """Console script entry point: ``raas-mcp-server``."""
    import sys

    args = sys.argv[1:]
    if args and args[0] in ("-h", "--help"):
        print(
            "raas-mcp-server — Salt RaaS MCP server (stdio transport)\n"
            "\n"
            "Usage:\n"
            "  raas-mcp-server\n"
            "\n"
            "The server is intended to be launched by an MCP client (Cursor, Claude Desktop,\n"
            "etc.) via the mcp.json / claude_desktop_config.json configuration, not directly\n"
            "from the terminal.\n"
            "\n"
            "Configuration is read from ~/.salt/config.yml.\n"
            "See the README.md for full setup instructions."
        )
        sys.exit(0)
    if args and args[0] in ("-V", "--version"):
        from raas_mcp import __version__
        print(f"raas-mcp-server {__version__}")
        sys.exit(0)
    asyncio.run(run())
