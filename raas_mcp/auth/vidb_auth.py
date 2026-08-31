"""VCF SSO / VIDB JWT validation for the RaaS MCP server.

Implements the VIDB JWT path described in specs/010-mcp-remote-transport/research.md
Section 11 and data-model.md VidbJwtValidator.

Architecture
------------
* ``VidbJwtValidator.create(cfg)`` is an async factory that performs OIDC
  discovery at startup to locate the JWKS URI; it NEVER raises — on failure
  it returns a disabled validator and logs a WARNING so the server degrades
  gracefully to opaque-token-only mode.

* ``validate(token)`` verifies the JWT signature + claims and returns an
  ``AccessToken`` for use by the MCP auth middleware.

* ``_is_vidb_jwt(token)`` is a lightweight discriminator: it peeks at the
  raw ``iss`` claim without signature verification to decide whether the
  token should be handled by this validator.

Token overflow (``ovl`` claim)
--------------------------------
VIDB may issue JWTs whose ``authorization_details`` payload exceeds 8 KB.
In that case VIDB sets ``"ovl": true`` and omits ``authorization_details``.
The MCP server skips the ``authorization_details`` role check for overflow
tokens; the RaaS HTTP layer re-validates privileges on each RPC call.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import jwt
from jwt import PyJWKClient, PyJWTError
from mcp.server.auth.middleware.bearer_auth import AccessToken

if TYPE_CHECKING:
    from raas_mcp.http_config import HttpServerConfig

logger = logging.getLogger(__name__)

# Algorithms accepted from VIDB (RSA and EC families)
_VIDB_ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"]

# Leeway applied to ``exp`` / ``nbf`` checks (seconds)
_JWT_LEEWAY = 30


class InvalidVidbTokenError(Exception):
    """Raised when a VIDB JWT is structurally valid but fails signature or claims checks."""


@dataclass
class VidbClaimsResult:
    """Internal result type carrying validated claims from a VIDB JWT."""

    sub: str
    exp: int
    has_overflow: bool
    authorization_details: list[dict[str, Any]] = field(default_factory=list)


class VidbJwtValidator:
    """Validates VIDB-issued JWT Bearer tokens.

    Instances are created via the async factory ``VidbJwtValidator.create(cfg)``.
    Callers must not instantiate this class directly.
    """

    def __init__(
        self,
        issuer_url: str,
        jwks_client: PyJWKClient | None,
        *,
        is_enabled: bool,
    ) -> None:
        self._issuer_url = issuer_url
        self._jwks_client = jwks_client
        self._is_enabled = is_enabled

    # ------------------------------------------------------------------
    # Public factory (async — performs OIDC discovery)
    # ------------------------------------------------------------------

    @classmethod
    async def create(cls, cfg: "HttpServerConfig") -> "VidbJwtValidator":
        """Async factory — discovers JWKS URI via OIDC and returns a validator.

        Always returns a ``VidbJwtValidator``; on discovery failure it returns
        one with ``_is_enabled=False`` after logging a WARNING.
        """
        issuer_url = cfg.vidb_issuer_url or ""

        if not issuer_url:
            return cls(issuer_url="", jwks_client=None, is_enabled=False)

        try:
            import httpx  # runtime import — confirmed available

            discovery_url = f"{issuer_url.rstrip('/')}/.well-known/openid-configuration"
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(discovery_url)
                resp.raise_for_status()
                oidc_config = resp.json()

            jwks_uri: str = oidc_config["jwks_uri"]
            jwks_client = PyJWKClient(
                jwks_uri,
                lifespan=cfg.vidb_jwks_refresh_interval_seconds,
                cache_keys=True,
            )
            logger.info(
                "VIDB JWT path enabled — OIDC issuer: %s  JWKS URI: %s",
                issuer_url,
                jwks_uri,
            )
            return cls(issuer_url=issuer_url, jwks_client=jwks_client, is_enabled=True)

        except Exception as exc:
            logger.warning(
                "VIDB OIDC discovery failed (%s): %s — VIDB JWT path disabled",
                issuer_url,
                exc,
            )
            return cls(issuer_url=issuer_url, jwks_client=None, is_enabled=False)

    # ------------------------------------------------------------------
    # Public validation entry-point
    # ------------------------------------------------------------------

    def validate(self, token: str) -> AccessToken | None:
        """Validate a VIDB JWT Bearer token.

        Returns
        -------
        AccessToken
            When the token is a valid, unexpired VIDB JWT with the correct
            ``iss`` claim and (when not overflow) a ``vcf_salt_operations``
            entry in ``authorization_details``.
        None
            When the token does not belong to this validator (wrong or absent
            ``iss`` claim, or validator is disabled).

        Raises
        ------
        InvalidVidbTokenError
            When the token looks like a VIDB JWT but fails signature
            verification, has expired, or is missing required claims.
        """
        if not self._is_enabled:
            return None

        if not self._is_vidb_jwt(token):
            return None

        if self._jwks_client is None:
            raise InvalidVidbTokenError("VIDB JWT validator has no JWKS client")

        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key.key,
                algorithms=_VIDB_ALGORITHMS,
                options={"require": ["exp", "iss", "sub"], "leeway": _JWT_LEEWAY},
            )
        except PyJWTError as exc:
            raise InvalidVidbTokenError(f"VIDB JWT validation failed: {exc}") from exc

        # Overflow: VIDB omits authorization_details when payload is too large.
        # RaaS re-validates privileges per RPC call; we skip the role check here.
        has_overflow = bool(claims.get("ovl"))
        if not has_overflow:
            _validate_authorization_details(claims)

        sub: str = claims["sub"]
        exp: int = int(claims["exp"])
        return AccessToken(token=token, client_id=sub, scopes=["raas"], expires_at=exp)

    # ------------------------------------------------------------------
    # Lightweight JWT discriminator
    # ------------------------------------------------------------------

    def _is_vidb_jwt(self, token: str) -> bool:
        """Return True when *token* carries an ``iss`` claim matching this validator's issuer.

        Performs no signature verification — used only to route tokens.
        Returns False on any decode error.
        """
        try:
            unverified = jwt.decode(
                token,
                options={"verify_signature": False, "verify_exp": False},
                algorithms=_VIDB_ALGORITHMS,
            )
            return unverified.get("iss") == self._issuer_url
        except (PyJWTError, Exception):
            return False


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _validate_authorization_details(claims: dict[str, Any]) -> None:
    """Raise ``InvalidVidbTokenError`` if the JWT lacks a ``vcf_salt_operations`` entry.

    Parameters
    ----------
    claims:
        Decoded JWT payload dictionary.
    """
    details = claims.get("authorization_details")
    if not isinstance(details, list):
        raise InvalidVidbTokenError(
            "VIDB JWT missing 'authorization_details' claim (non-overflow token)"
        )
    for entry in details:
        if isinstance(entry, dict) and entry.get("type") == "vcf_salt_operations":
            return
    raise InvalidVidbTokenError(
        "VIDB JWT 'authorization_details' does not contain a 'vcf_salt_operations' entry"
    )
