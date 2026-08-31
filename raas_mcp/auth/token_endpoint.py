"""POST /token endpoint: exchanges RaaS Basic credentials for a Bearer token.

Flow
----
1. Client sends ``Authorization: Basic base64(user:pass)`` to ``POST /token``.
2. We attempt a lightweight RaaS API call (``api.get_versions``) using those
   credentials to verify they are valid.
3. On success, we mint a short-lived opaque Bearer token and return it.
4. On RaaS auth failure (401/403) or connection error we return a 401.

Credentials are **never** stored in plaintext beyond the TokenEntry TTL.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from raas_mcp.auth.token_store import TokenStore

logger = logging.getLogger(__name__)


def _parse_basic_auth(request: Request) -> tuple[str, str] | None:
    """Extract (username, password) from the Basic Authorization header.

    Returns ``None`` if the header is absent, malformed, or not Basic auth.
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("basic "):
        return None
    try:
        decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
        user, _, password = decoded.partition(":")
        if not user:
            return None
        return user, password
    except Exception:
        return None


def _validate_raas_credentials(
    raas_url: str,
    raas_user: str,
    raas_password: str,
    *,
    insecure: bool = False,
    timeout: float = 30.0,
) -> bool:
    """Return True if the credentials are accepted by RaaS.

    Performs a single lightweight ``api.get_versions()`` call.  Catches all
    exceptions so the token endpoint never leaks internal stack traces.
    """
    try:
        from raas_mcp.raas_client import connect_from_mapping

        client = connect_from_mapping(
            {
                "raas": raas_url,
                "auth": f"{raas_user}:{raas_password}",
                "timeout": timeout,
                "insecure": insecure,
            }
        )
        client.api.api.get_versions()
        return True
    except Exception as exc:
        msg = str(exc).lower()
        if "401" in msg or "403" in msg or "unauthorized" in msg or "forbidden" in msg:
            logger.debug("RaaS credential validation failed for user=%s", raas_user)
        else:
            logger.warning(
                "RaaS credential validation error (user=%s): %s",
                raas_user,
                exc,
                exc_info=False,
            )
        return False


def build_token_handler(
    token_store: TokenStore,
    *,
    raas_url: str,
    raas_insecure: bool = False,
    raas_timeout: float = 30.0,
) -> Any:
    """Return a Starlette route handler for ``POST /token``."""

    async def token_handler(request: Request) -> Response:
        creds = _parse_basic_auth(request)
        if creds is None:
            return JSONResponse(
                {"error": "invalid_request", "error_description": "Basic Authorization header required"},
                status_code=400,
                headers={"WWW-Authenticate": 'Basic realm="RaaS MCP"'},
            )

        raas_user, raas_password = creds
        valid = _validate_raas_credentials(
            raas_url,
            raas_user,
            raas_password,
            insecure=raas_insecure,
            timeout=raas_timeout,
        )
        if not valid:
            return JSONResponse(
                {
                    "error": "invalid_client",
                    "error_description": "RaaS credentials are invalid or RaaS is unreachable",
                },
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="RaaS MCP"'},
            )

        token = token_store.create(raas_user, raas_password)
        entry = token_store.lookup(token)
        expires_in = int(entry.expires_at - entry.issued_at) if entry else 3600

        try:
            from raas_mcp.metrics import TOKEN_ISSUES_TOTAL
            TOKEN_ISSUES_TOTAL.inc()
        except Exception:
            pass

        return JSONResponse(
            {
                "access_token": token,
                "token_type": "bearer",
                "expires_in": expires_in,
            },
            status_code=200,
        )

    return token_handler
