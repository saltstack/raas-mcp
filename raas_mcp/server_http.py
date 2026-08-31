"""Streamable HTTP transport for the RaaS MCP server (MCP 2025-03-26).

Builds an ASGI application with:
- T019a: Public routes — POST /token, GET /.well-known/oauth-protected-resource,
         GET /health/ready, GET /health/live
- T019b: Auth wiring — BearerAuthBackend → AuthenticationMiddleware →
         AuthContextMiddleware; MCP endpoint (POST /mcp) behind RequireAuthMiddleware;
         per-request RaaS client built from token → TokenStore.lookup() (opaque path)
         or VIDB JWT passthrough (OIDC SSO path)
- T019c: CORSMiddleware as the outermost wrapper (when origins are configured)
- T020:  SSE keepalive background task (heartbeat log lines + watchdog)
- T046:  ``build_http_app_with_vidb()`` async factory that performs OIDC discovery
         before constructing the Starlette app

Call ``build_http_app_with_vidb(cfg, token_store)`` for new deployments.
``build_http_app(cfg, token_store, vidb_validator)`` is the synchronous
lower-level constructor used by tests.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from mcp import types as mcp_types
from mcp.server import Server
from mcp.server.auth.middleware.auth_context import AuthContextMiddleware, get_access_token
from mcp.server.auth.middleware.bearer_auth import (
    BearerAuthBackend,
    RequireAuthMiddleware,
)
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from raas_mcp import catalog, metrics
from raas_mcp.auth.protected_resource import build_protected_resource_handler
from raas_mcp.auth.token_endpoint import build_token_handler
from raas_mcp.auth.token_store import TokenStore
from raas_mcp.auth.verifier import DualModeTokenVerifier
from raas_mcp.auth.vidb_auth import VidbJwtValidator
from raas_mcp.dispatcher import dispatch
from raas_mcp.http_config import HttpServerConfig

logger = logging.getLogger(__name__)


class _PathDispatcher:
    """Simple ASGI path dispatcher.

    Routes ``/mcp`` (and ``/mcp/*``) to ``mcp_app``; everything else (including
    lifespan events) to ``public_app``.

    Using a manual dispatcher avoids Starlette's ``Mount``→redirect-to-slash
    behavior that would turn ``POST /mcp`` into a 307.
    """

    def __init__(self, public_app: ASGIApp, mcp_app: ASGIApp) -> None:
        self._public = public_app
        self._mcp = mcp_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._public(scope, receive, send)
            return
        path: str = scope.get("path", "")
        if path == "/mcp" or path.startswith("/mcp/"):
            await self._mcp(scope, receive, send)
        else:
            await self._public(scope, receive, send)


def _build_mcp_server(
    cfg: HttpServerConfig,
    token_store: TokenStore,
    vidb_validator: VidbJwtValidator | None,
) -> Server:
    """Build a low-level MCP Server with tool handlers that use per-request RaaS creds."""
    server = Server("raas-mcp-server")
    tool_list = catalog.build_tool_list(allowed=cfg.allowed_tools)
    catalog_entries = catalog.get_catalog_entries(allowed=cfg.allowed_tools)

    @server.list_tools()
    async def _list_tools() -> list[mcp_types.Tool]:
        return tool_list

    @server.call_tool()
    async def _call_tool(
        name: str, arguments: dict[str, Any] | None
    ) -> list[mcp_types.TextContent]:
        access_token = get_access_token()
        if access_token is None:
            from raas_mcp.errors import ErrorCode, error_result
            return error_result(ErrorCode.RAAS_RPC_ERROR, "No authenticated user in request context")

        bearer_token_str = access_token.token

        # Determine which credential path to use
        entry = token_store.lookup(bearer_token_str)
        if entry is not None:
            # Opaque path — use stored user/password
            with metrics.track_request():
                return await dispatch(
                    tool_name=name,
                    arguments=arguments or {},
                    catalog_entries=catalog_entries,
                    approval_gate=cfg.approval_gate,
                    ctx=None,
                    raas_user=entry.raas_user,
                    raas_password=entry.raas_password,
                    bearer_token=bearer_token_str,
                    raas_url=cfg.raas_url,
                    raas_insecure=cfg.raas_insecure,
                    raas_timeout=cfg.raas_timeout,
                    token_store=token_store,
                )
        else:
            # VIDB JWT path — forward the raw JWT as a Bearer token to RaaS
            with metrics.track_request():
                return await dispatch(
                    tool_name=name,
                    arguments=arguments or {},
                    catalog_entries=catalog_entries,
                    approval_gate=cfg.approval_gate,
                    ctx=None,
                    raas_url=cfg.raas_url,
                    raas_insecure=cfg.raas_insecure,
                    raas_timeout=cfg.raas_timeout,
                    vidb_jwt=bearer_token_str,
                )

    return server


def build_http_app(
    cfg: HttpServerConfig,
    token_store: TokenStore,
    vidb_validator: VidbJwtValidator | None = None,
) -> tuple[ASGIApp, StreamableHTTPSessionManager]:
    """Build and return the composed ASGI app + session manager.

    The caller is responsible for:
    1. Serving the returned app on ``cfg.mcp_port``.
    2. Serving ``metrics.metrics_app`` on ``cfg.metrics_port``.

    Returns
    -------
    (app, session_manager)
    """
    mcp_server = _build_mcp_server(cfg, token_store, vidb_validator)
    session_manager = StreamableHTTPSessionManager(mcp_server, stateless=True)
    verifier = DualModeTokenVerifier(token_store, vidb_validator)

    # -----------------------------------------------------------------------
    # T019b: raw MCP ASGI app → wrapped with auth enforcement
    # -----------------------------------------------------------------------

    async def _mcp_asgi(scope: Scope, receive: Receive, send: Send) -> None:
        await session_manager.handle_request(scope, receive, send)

    authed_mcp: ASGIApp = RequireAuthMiddleware(
        AuthContextMiddleware(_mcp_asgi),
        required_scopes=["raas"],
    )

    # -----------------------------------------------------------------------
    # T019a: Public routes (Starlette app also carries the lifespan)
    # -----------------------------------------------------------------------

    async def health_ready(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ready"})

    async def health_live(request: Request) -> JSONResponse:
        return JSONResponse({"status": "live"})

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        async with session_manager.run():
            yield

    public_app: ASGIApp = Starlette(
        routes=[
            Route("/token", build_token_handler(
                token_store,
                raas_url=cfg.raas_url,
                raas_insecure=cfg.raas_insecure,
                raas_timeout=cfg.raas_timeout,
            ), methods=["POST"]),
            Route(
                "/.well-known/oauth-protected-resource",
                build_protected_resource_handler(cfg=cfg, vidb_validator=vidb_validator),
            ),
            Route("/health/ready", health_ready),
            Route("/health/live", health_live),
        ],
        lifespan=lifespan,
    )

    # -----------------------------------------------------------------------
    # T019b continued: path dispatcher
    # -----------------------------------------------------------------------
    app: ASGIApp = _PathDispatcher(public_app, authed_mcp)

    # -----------------------------------------------------------------------
    # T019b: BearerAuth middleware wraps the whole dispatcher so that
    # AuthenticationMiddleware populates request.user before any route runs.
    # RequireAuthMiddleware (already inside _mcp_asgi) enforces the check.
    # -----------------------------------------------------------------------
    app = AuthenticationMiddleware(app, backend=BearerAuthBackend(verifier))

    # -----------------------------------------------------------------------
    # T019c: CORS as the outermost layer when origins are configured.
    # Mcp-Session-Id is required for MCP session resumption (FR-002a).
    # -----------------------------------------------------------------------
    if cfg.cors_allowed_origins:
        from starlette.middleware.cors import CORSMiddleware
        app = CORSMiddleware(
            app,
            allow_origins=cfg.cors_allowed_origins,
            allow_methods=["GET", "POST", "OPTIONS", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "Accept", "Mcp-Session-Id"],
        )

    return app, session_manager


async def build_http_app_with_vidb(
    cfg: HttpServerConfig,
    token_store: TokenStore,
) -> tuple[ASGIApp, StreamableHTTPSessionManager]:
    """T046: Async factory that performs OIDC discovery before building the ASGI app.

    This is the recommended entry-point for ``__main__.py`` and production use.
    It:
    1. Calls ``await VidbJwtValidator.create(cfg)`` to perform OIDC discovery.
    2. Logs the startup outcome (enabled/disabled).
    3. Delegates to ``build_http_app(cfg, token_store, vidb_validator)``.

    Using an async factory keeps the I/O out of ``__main__.py`` and
    co-located with the Starlette app construction.
    """
    vidb_validator = await VidbJwtValidator.create(cfg)
    if vidb_validator._is_enabled:
        logger.info(
            "VIDB JWT authentication enabled (issuer: %s)", cfg.vidb_issuer_url
        )
    else:
        if cfg.vidb_issuer_url:
            logger.warning(
                "VIDB JWT authentication DISABLED — OIDC discovery failed for %s",
                cfg.vidb_issuer_url,
            )
        else:
            logger.debug("VIDB JWT authentication not configured")

    return build_http_app(cfg, token_store, vidb_validator)


async def _keepalive_task(interval: int) -> None:
    """T020: periodic heartbeat log (actual SSE keepalives handled by MCP SDK).

    Started by ``__main__.py`` in HTTP mode alongside the Uvicorn server tasks.
    """
    while True:
        await asyncio.sleep(interval)
        logger.debug("keepalive ping (interval=%ds)", interval)
