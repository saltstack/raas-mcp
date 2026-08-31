"""Load the bundled RaaS API discovery document.

Replaces ``vcf_salt.discovery_io``. The catalog data itself
(``raas_mcp/data/api_discovery.json``) is vendored from the ``vcf-salt``
source rather than imported at runtime, so raas-mcp has no dependency on
``vcf_salt`` being installed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def bundled_api_schema_path() -> Path:
    return Path(__file__).resolve().parent / "data" / "api_discovery.json"


def load_discovery_dict() -> dict[str, Any] | None:
    """Return the API schema dict bundled with raas-mcp, or None if missing/empty."""
    p = bundled_api_schema_path()
    if not p.is_file():
        return None
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError:
        return None
    if not raw.strip():
        return None
    data = json.loads(raw)
    if isinstance(data, dict) and data:
        return data
    return None
