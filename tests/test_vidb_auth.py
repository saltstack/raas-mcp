"""Unit tests for raas_mcp.auth.vidb_auth (T044).

Test scenarios as specified in tasks.md T044:
  a. create() with mocked reachable OIDC endpoint → _is_enabled=True
  b. create() with mocked connection error → _is_enabled=False, no exception
  c. create() with cfg.vidb_issuer_url=None → _is_enabled=False
  d. validate() when _is_enabled=False → returns None
  e. validate() with non-JWT string → returns None (no exception)
  f. validate() with JWT iss != _issuer_url → returns None
  g. validate() with valid mocked VIDB JWT → returns AccessToken with client_id==sub
  h. validate() with expired JWT → raises InvalidVidbTokenError
  i. validate() with ovl claim present → skips authorization_details, returns AccessToken
  j. validate() without vcf_salt_operations in authorization_details → raises InvalidVidbTokenError
  k. (JWKS re-fetch covered by PyJWT/PyJWKClient library behaviour)
  l. _is_vidb_jwt() with malformed token string → returns False
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from cryptography.hazmat.backends import default_backend
from jwt import PyJWKClient

from raas_mcp.auth.vidb_auth import (
    InvalidVidbTokenError,
    VidbJwtValidator,
    _validate_authorization_details,
)
from raas_mcp.http_config import HttpServerConfig

# ---------------------------------------------------------------------------
# Test key material (generated once at module load for determinism)
# ---------------------------------------------------------------------------

def _generate_rsa_key():
    from cryptography.hazmat.primitives.asymmetric import rsa as _rsa
    return _rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )


_PRIVATE_KEY = _generate_rsa_key()
_PUBLIC_KEY = _PRIVATE_KEY.public_key()

_ISSUER = "https://vidb.test/oidc/tenant"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_token(
    *,
    iss: str = _ISSUER,
    sub: str = "user@vsphere.local",
    exp_offset: int = 3600,
    include_auth_details: bool = True,
    ovl: bool = False,
    extra: dict | None = None,
) -> str:
    """Sign a JWT with the test RSA private key."""
    now = int(time.time())
    payload: dict = {
        "iss": iss,
        "sub": sub,
        "iat": now,
        "exp": now + exp_offset,
    }
    if ovl:
        payload["ovl"] = True
    elif include_auth_details:
        payload["authorization_details"] = [
            {"type": "vcf_salt_operations", "roles": ["admin"]}
        ]
    if extra:
        payload.update(extra)
    return jwt.encode(payload, _PRIVATE_KEY, algorithm="RS256")


def _make_validator(*, enabled: bool = True) -> VidbJwtValidator:
    """Build a VidbJwtValidator with a real PyJWKClient backed by the test key."""
    if not enabled:
        return VidbJwtValidator(issuer_url=_ISSUER, jwks_client=None, is_enabled=False)

    # Build a minimal PyJWKClient that wraps the test public key
    jwks_client = MagicMock(spec=PyJWKClient)
    signing_key_mock = MagicMock()
    signing_key_mock.key = _PUBLIC_KEY
    jwks_client.get_signing_key_from_jwt.return_value = signing_key_mock

    return VidbJwtValidator(
        issuer_url=_ISSUER,
        jwks_client=jwks_client,
        is_enabled=True,
    )


# ---------------------------------------------------------------------------
# a. create() with reachable OIDC endpoint → _is_enabled=True
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_reachable_oidc():
    cfg = HttpServerConfig(
        raas_url="http://raas.test",
        vidb_issuer_url=_ISSUER,
        vidb_jwks_refresh_interval_seconds=300,
    )
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "jwks_uri": f"{_ISSUER}/.well-known/jwks.json",
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client), \
         patch("raas_mcp.auth.vidb_auth.PyJWKClient") as mock_jwks_cls:
        mock_jwks_cls.return_value = MagicMock()
        validator = await VidbJwtValidator.create(cfg)

    assert validator._is_enabled is True
    assert validator._jwks_client is not None


# ---------------------------------------------------------------------------
# b. create() with mocked connection error → _is_enabled=False, no exception
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_connection_error():
    cfg = HttpServerConfig(
        raas_url="http://raas.test",
        vidb_issuer_url=_ISSUER,
        vidb_jwks_refresh_interval_seconds=300,
    )
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=ConnectionError("refused"))

    with patch("httpx.AsyncClient", return_value=mock_client):
        validator = await VidbJwtValidator.create(cfg)

    assert validator._is_enabled is False
    assert validator._jwks_client is None


# ---------------------------------------------------------------------------
# c. create() with vidb_issuer_url=None → _is_enabled=False
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_no_issuer():
    cfg = HttpServerConfig(raas_url="http://raas.test")
    validator = await VidbJwtValidator.create(cfg)
    assert validator._is_enabled is False


# ---------------------------------------------------------------------------
# d. validate() when _is_enabled=False → returns None
# ---------------------------------------------------------------------------

def test_validate_disabled_returns_none():
    validator = _make_validator(enabled=False)
    result = validator.validate(_make_token())
    assert result is None


# ---------------------------------------------------------------------------
# e. validate() with non-JWT string → returns None (no exception)
# ---------------------------------------------------------------------------

def test_validate_non_jwt_returns_none():
    validator = _make_validator()
    result = validator.validate("not.a.jwt")
    assert result is None


# ---------------------------------------------------------------------------
# f. validate() with JWT iss != _issuer_url → returns None
# ---------------------------------------------------------------------------

def test_validate_wrong_issuer_returns_none():
    validator = _make_validator()
    token = _make_token(iss="https://other-vidb.example.com/oidc/t1")
    result = validator.validate(token)
    assert result is None


# ---------------------------------------------------------------------------
# g. validate() with valid VIDB JWT → returns AccessToken with client_id==sub
# ---------------------------------------------------------------------------

def test_validate_valid_jwt_returns_access_token():
    validator = _make_validator()
    sub = "alice@vsphere.local"
    token = _make_token(sub=sub)
    access_token = validator.validate(token)
    assert access_token is not None
    assert access_token.client_id == sub
    assert "raas" in access_token.scopes
    assert access_token.token == token


# ---------------------------------------------------------------------------
# h. validate() with expired JWT → raises InvalidVidbTokenError
# ---------------------------------------------------------------------------

def test_validate_expired_jwt_raises():
    validator = _make_validator()
    token = _make_token(exp_offset=-100)
    with pytest.raises(InvalidVidbTokenError):
        validator.validate(token)


# ---------------------------------------------------------------------------
# i. validate() with ovl claim → skips authorization_details, returns AccessToken
# ---------------------------------------------------------------------------

def test_validate_overflow_token_skips_auth_details():
    validator = _make_validator()
    # ovl=True, no authorization_details
    token = _make_token(ovl=True, include_auth_details=False)
    access_token = validator.validate(token)
    assert access_token is not None
    assert access_token.client_id == "user@vsphere.local"


# ---------------------------------------------------------------------------
# j. validate() without vcf_salt_operations → raises InvalidVidbTokenError
# ---------------------------------------------------------------------------

def test_validate_missing_vcf_salt_operations_raises():
    validator = _make_validator()
    token = _make_token(
        include_auth_details=False,
        extra={"authorization_details": [{"type": "other_service"}]},
    )
    with pytest.raises(InvalidVidbTokenError, match="vcf_salt_operations"):
        validator.validate(token)


# ---------------------------------------------------------------------------
# l. _is_vidb_jwt() with malformed token → returns False
# ---------------------------------------------------------------------------

def test_is_vidb_jwt_malformed_returns_false():
    validator = _make_validator()
    assert validator._is_vidb_jwt("not-a-jwt") is False
    assert validator._is_vidb_jwt("") is False
    assert validator._is_vidb_jwt("a.b") is False  # only two segments


# ---------------------------------------------------------------------------
# _validate_authorization_details helper
# ---------------------------------------------------------------------------

def test_validate_auth_details_passes_with_correct_type():
    claims = {"authorization_details": [{"type": "vcf_salt_operations"}]}
    _validate_authorization_details(claims)  # should not raise


def test_validate_auth_details_raises_without_type():
    claims = {"authorization_details": [{"type": "other"}]}
    with pytest.raises(InvalidVidbTokenError):
        _validate_authorization_details(claims)


def test_validate_auth_details_raises_without_list():
    with pytest.raises(InvalidVidbTokenError):
        _validate_authorization_details({"authorization_details": "not-a-list"})


def test_validate_auth_details_raises_when_missing():
    with pytest.raises(InvalidVidbTokenError):
        _validate_authorization_details({})
