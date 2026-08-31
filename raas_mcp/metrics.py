"""Prometheus metrics for the RaaS MCP HTTP server.

Exposes:
- ``raas_mcp_active_sessions`` (Gauge)   — currently active MCP sessions
- ``raas_mcp_requests_total`` (Counter)  — total tool-call requests by status
- ``raas_mcp_token_issues_total`` (Counter) — Bearer tokens issued
- ``raas_mcp_token_invalidations_total`` (Counter) — tokens invalidated (e.g. RaaS 401)

The ``/metrics`` ASGI app (``metrics_app``) is served on a *separate* port so
that it is never reachable through the public MCP ingress.
"""

from __future__ import annotations

import contextlib
from typing import Any

from prometheus_client import REGISTRY, Counter, Gauge, make_asgi_app

# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------

ACTIVE_SESSIONS = Gauge(
    "raas_mcp_active_sessions",
    "Number of currently active MCP sessions",
)

REQUESTS_TOTAL = Counter(
    "raas_mcp_requests_total",
    "Total tool-call requests handled",
    ["status"],  # labels: "success" | "error"
)

TOKEN_ISSUES_TOTAL = Counter(
    "raas_mcp_token_issues_total",
    "Total Bearer tokens issued via POST /token",
)

TOKEN_INVALIDATIONS_TOTAL = Counter(
    "raas_mcp_token_invalidations_total",
    "Total Bearer tokens invalidated (e.g. on RaaS 401/403)",
)

# ---------------------------------------------------------------------------
# ASGI app for the dedicated /metrics port
# ---------------------------------------------------------------------------

metrics_app = make_asgi_app(registry=REGISTRY)


# ---------------------------------------------------------------------------
# Context manager helper used by server_http.py tool handler
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def track_request():
    """Context manager that increments ACTIVE_SESSIONS and records success/error."""
    ACTIVE_SESSIONS.inc()
    status = "success"
    try:
        yield
    except Exception:
        status = "error"
        raise
    finally:
        ACTIVE_SESSIONS.dec()
        REQUESTS_TOTAL.labels(status=status).inc()
