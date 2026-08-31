"""Entry point: ``python -m raas_mcp``.

Supports two transport modes selected via ``--transport``:

  stdio  (default) — start the existing stdio MCP server (spec-008 behaviour)
  http             — start the Streamable HTTP MCP server (spec-010)

HTTP mode reads all configuration from environment variables (see
``raas_mcp.http_config.HttpServerConfig`` for the full list).
"""

from __future__ import annotations

import argparse
import asyncio
import sys


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="raas-mcp-server",
        description="Salt RaaS MCP server",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="Transport mode: 'stdio' (default) or 'http'",
    )
    parser.add_argument(
        "-V", "--version",
        action="store_true",
        help="Print version and exit",
    )
    return parser.parse_args(argv)


async def _run_http() -> None:
    """Start the HTTP-mode server on two Uvicorn instances (MCP + metrics)."""
    try:
        import uvicorn
    except ImportError:
        print("raas-mcp-server: uvicorn is required for HTTP transport mode.", file=sys.stderr)
        sys.exit(1)

    from raas_mcp.auth.token_store import TokenStore
    from raas_mcp.http_config import load as load_http_cfg
    from raas_mcp.metrics import metrics_app
    from raas_mcp.server_http import _keepalive_task, build_http_app_with_vidb

    cfg = load_http_cfg()
    token_store = TokenStore(token_ttl_seconds=cfg.token_ttl_seconds)
    app, _session_mgr = await build_http_app_with_vidb(cfg, token_store)

    ssl_kwargs: dict = {}
    if cfg.tls_enabled and cfg.tls_cert_path and cfg.tls_key_path:
        ssl_kwargs = {
            "ssl_certfile": cfg.tls_cert_path,
            "ssl_keyfile": cfg.tls_key_path,
        }

    mcp_config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=cfg.mcp_port,
        log_level="info",
        **ssl_kwargs,
    )
    metrics_config = uvicorn.Config(
        metrics_app,
        host="0.0.0.0",
        port=cfg.metrics_port,
        log_level="warning",
    )

    mcp_server = uvicorn.Server(mcp_config)
    metrics_server = uvicorn.Server(metrics_config)

    async def serve_mcp() -> None:
        await mcp_server.serve()

    async def serve_metrics() -> None:
        await metrics_server.serve()

    async def keepalive() -> None:
        await _keepalive_task(cfg.keepalive_interval_seconds)

    await asyncio.gather(
        serve_mcp(),
        serve_metrics(),
        keepalive(),
        return_exceptions=False,
    )


def main(argv: list[str] | None = None) -> None:
    """Console-script entry point."""
    args = _parse_args(argv)

    if args.version:
        from raas_mcp import __version__
        print(f"raas-mcp-server {__version__}")
        sys.exit(0)

    if args.transport == "http":
        asyncio.run(_run_http())
    else:
        from raas_mcp.server import main as stdio_main
        stdio_main()


if __name__ == "__main__":
    main()
