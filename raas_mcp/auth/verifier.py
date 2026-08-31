"""DualModeTokenVerifier — routes Bearer tokens to opaque or VIDB JWT path.

Two authentication paths are supported:

1. **Opaque path** (existing): callers exchange RaaS credentials for a short-lived
   opaque token at ``POST /token``; the token is looked up in ``TokenStore``.

2. **VIDB JWT path** (new): VCF SSO callers present a VIDB-issued JWT directly;
   ``VidbJwtValidator`` verifies the signature and claims without any exchange step.

Routing logic
-------------
* If ``_vidb_validator`` is not None and ``_is_enabled``:
    - Call ``_vidb_validator._is_vidb_jwt(token)`` to discriminate.
    - If the token looks like a VIDB JWT, call ``_vidb_validator.validate(token)``.
      - On ``InvalidVidbTokenError`` → return ``None`` (invalid VIDB token).
      - On ``AccessToken`` → return it.
    - If the token does NOT look like a VIDB JWT, fall through to opaque path.
* Look up the token in ``TokenStore``; return ``AccessToken`` on hit or ``None``.
"""

from __future__ import annotations

import logging

from mcp.server.auth.middleware.bearer_auth import AccessToken, TokenVerifier

from raas_mcp.auth.token_store import TokenStore
from raas_mcp.auth.vidb_auth import InvalidVidbTokenError, VidbJwtValidator

logger = logging.getLogger(__name__)


class DualModeTokenVerifier:
    """Implements the MCP :class:`TokenVerifier` protocol with dual-mode routing.

    Parameters
    ----------
    token_store:
        Opaque token store for the ``POST /token`` path.
    vidb_validator:
        Optional VIDB JWT validator; ``None`` means VIDB path is disabled.
    """

    def __init__(
        self,
        token_store: TokenStore,
        vidb_validator: VidbJwtValidator | None = None,
    ) -> None:
        self._store = token_store
        self._vidb_validator = vidb_validator

    async def verify_token(self, token: str) -> AccessToken | None:
        # 1. Try VIDB JWT path when validator is available and enabled
        if self._vidb_validator is not None and self._vidb_validator._is_enabled:
            if self._vidb_validator._is_vidb_jwt(token):
                try:
                    result = self._vidb_validator.validate(token)
                    if result is not None:
                        return result
                except InvalidVidbTokenError as exc:
                    logger.debug("VIDB JWT validation rejected: %s", exc)
                    return None

        # 2. Opaque token path
        entry = self._store.lookup(token)
        if entry is None:
            return None
        return AccessToken(
            token=token,
            client_id=entry.raas_user,
            scopes=["raas"],
            expires_at=int(entry.expires_at),
        )


# Satisfy the structural TokenVerifier protocol at import time.
_: TokenVerifier = DualModeTokenVerifier.__new__(DualModeTokenVerifier)  # type: ignore[assignment]
