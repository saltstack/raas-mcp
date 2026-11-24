#!/usr/bin/env python3
"""Offline release checks for accidental secrets and packaging regressions.

Modeled on the equivalent script in the sibling public repo `raas-cli`
(https://github.com/saltstack/raas-cli).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    init = (ROOT / "raas_mcp" / "__init__.py").read_text(encoding="utf-8")
    project_version = re.search(r'^version = "([^"]+)"', pyproject, re.M)
    module_version = re.search(r'^__version__ = "([^"]+)"', init, re.M)
    versions_match = (
        project_version and module_version and project_version.group(1) == module_version.group(1)
    )
    if not versions_match:
        fail("pyproject and module versions do not match")

    banned_patterns = {
        "private key material": re.compile(
            r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"
        ),
        "GitHub access token": re.compile(
            r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"
        ),
        "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
        "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
        "Broadcom-internal Artifactory URL as a hard default": re.compile(
            r"packages\.vcfd\.broadcom\.net"
        ),
    }
    scan_extensions = {".py", ".md", ".yaml", ".yml", ".toml", ".txt", ".sh"}
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in scan_extensions:
            continue
        if any(
            part in {"dist", "build", ".git", ".venv", "venv", ".ruff_cache", "__pycache__"}
            for part in path.parts
        ):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for label, pattern in banned_patterns.items():
            if pattern.search(text):
                fail(f"possible {label} in {path.relative_to(ROOT)}")

    # Smoke test: build the tool catalog and list a few known tools, in-process,
    # via the same in-memory mcp.Client/Server pairing used by
    # tests/test_server_integration.py — no real RaaS connection required.
    from unittest.mock import MagicMock

    import anyio
    from mcp import Client
    from mcp import types as mcp_types
    from mcp.server import Server, ServerRequestContext

    from raas_mcp import catalog as catalog_mod
    from raas_mcp import dispatcher

    async def _smoke() -> int:
        tool_list = catalog_mod.build_tool_list(allowed=None)
        catalog_entries = catalog_mod.get_catalog_entries(allowed=None)
        if not tool_list:
            fail("catalog produced zero tools from the bundled api_discovery.json")

        mock_client = MagicMock()
        async def _list_tools(
            ctx: ServerRequestContext, params: mcp_types.PaginatedRequestParams | None
        ) -> mcp_types.ListToolsResult:
            return mcp_types.ListToolsResult(tools=tool_list)

        async def _call_tool(
            ctx: ServerRequestContext, params: mcp_types.CallToolRequestParams
        ) -> mcp_types.CallToolResult:
            content = await dispatcher.dispatch(
                tool_name=params.name,
                arguments=params.arguments or {},
                client=mock_client,
                catalog_entries=catalog_entries,
                approval_gate=[],
                ctx=None,
            )
            return mcp_types.CallToolResult(content=content)

        server = Server(
            "raas-mcp-server-release-check",
            on_list_tools=_list_tools,
            on_call_tool=_call_tool,
        )

        async with Client(server) as session:
            result = await session.list_tools()
            if not result.tools:
                fail("list_tools returned no tools over the MCP session")
        return len(tool_list)

    tool_count = anyio.run(_smoke)

    print(
        f"Release checks passed for raas-mcp-server {project_version.group(1)} "
        f"({tool_count} MCP tools in catalog)."
    )


if __name__ == "__main__":
    main()
