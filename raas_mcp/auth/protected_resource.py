"""GET /.well-known/oauth-protected-resource handler (RFC 9728).

Publishes the Protected Resource Metadata document so that MCP clients and
OAuth-aware tools can discover the token endpoint(s) automatically.

The document contains:
- ``resource``: canonical URI of this protected resource (from ``request.base_url``)
- ``authorization_servers``: list of authorization server URIs;
    always contains ``<resource>/token`` first (opaque Basic-Auth path);
    when VIDB is enabled, also contains the VIDB issuer URL (OIDC SSO path).
- ``bearer_methods_supported``: always ``["header"]``

The response conforms to
``specs/010-mcp-remote-transport/contracts/protected-resource-metadata.schema.json``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

if TYPE_CHECKING:
    from raas_mcp.auth.vidb_auth import VidbJwtValidator
    from raas_mcp.http_config import HttpServerConfig


def build_protected_resource_handler(
    *,
    cfg: HttpServerConfig,
    vidb_validator: VidbJwtValidator | None = None,
) -> Any:
    """Return a Starlette route handler for ``GET /.well-known/oauth-protected-resource``.

    Parameters
    ----------
    cfg:
        HTTP server configuration; used to read ``vidb_issuer_url``.
    vidb_validator:
        Optional VIDB JWT validator; when ``_is_enabled`` and ``cfg.vidb_issuer_url``
        is set, the VIDB issuer URL is appended to ``authorization_servers``.
    """

    async def protected_resource_handler(request: Request) -> Response:
        resource = str(request.base_url).rstrip("/")

        # First entry is always the server's own opaque /token endpoint.
        authorization_servers = [f"{resource}/token"]

        # Second entry added when VIDB is enabled — OIDC SSO callers use it to
        # discover that they can present VIDB JWTs directly at POST /mcp.
        if (
            cfg.vidb_issuer_url
            and vidb_validator is not None
            and vidb_validator._is_enabled
        ):
            authorization_servers.append(cfg.vidb_issuer_url)

        metadata = {
            "resource": resource,
            "authorization_servers": authorization_servers,
            "bearer_methods_supported": ["header"],
        }
        return JSONResponse(metadata, status_code=200)

    return protected_resource_handler
