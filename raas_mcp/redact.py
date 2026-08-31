"""Redact credentials from strings before printing to stderr or logs."""

from __future__ import annotations

import re


def redact_secrets(message: str) -> str:
    """Best-effort removal of userinfo in URLs and obvious ``user:pass`` URL forms."""
    if not message:
        return message
    # https://user:pass@host → https://*:*@host
    out = re.sub(r"(^[a-z][-+.a-z0-9]*://)([^:/@]+):([^@]+)@", r"\1*:*@", message, flags=re.IGNORECASE | re.MULTILINE)
    out = re.sub(r"(\s)([a-z][-+.a-z0-9]*://)([^:/@]+):([^@]+)@", r"\1\2*:*@", out, flags=re.IGNORECASE)
    return out
