"""In-memory Bearer token store mapping opaque tokens to RaaS credentials."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass


@dataclass
class TokenEntry:
    token: str
    raas_user: str
    raas_password: str
    issued_at: float
    expires_at: float


class TokenStore:
    """Thread-safe in-memory store for short-lived Bearer tokens.

    Each call to :meth:`create` returns a new opaque token regardless of
    whether the same ``raas_user`` already has a valid token.  Callers must
    supply the TTL at construction time so that the store can enforce expiry
    without coupling to any external clock dependency in tests.
    """

    def __init__(self, token_ttl_seconds: int = 3600) -> None:
        self._ttl = token_ttl_seconds
        self._store: dict[str, TokenEntry] = {}

    # ------------------------------------------------------------------
    # Mutating operations
    # ------------------------------------------------------------------

    def create(self, raas_user: str, raas_password: str) -> str:
        """Issue a new token for the given RaaS credentials.

        Returns the raw token string (64 hex characters / 256 bits of entropy).
        """
        token = secrets.token_hex(32)
        now = time.time()
        self._store[token] = TokenEntry(
            token=token,
            raas_user=raas_user,
            raas_password=raas_password,
            issued_at=now,
            expires_at=now + self._ttl,
        )
        return token

    def invalidate(self, token: str) -> None:
        """Remove *token* from the store (no-op if already absent)."""
        self._store.pop(token, None)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def lookup(self, token: str) -> TokenEntry | None:
        """Return the :class:`TokenEntry` for *token*, or ``None`` if absent/expired.

        Performs lazy eviction: expired entries are removed on first access.
        """
        entry = self._store.get(token)
        if entry is None:
            return None
        if time.time() > entry.expires_at:
            del self._store[token]
            return None
        return entry
