"""Unit tests for raas_mcp.metrics."""

from __future__ import annotations

import pytest


def test_metrics_module_imports():
    """All public symbols importable without errors."""
    from raas_mcp.metrics import (
        ACTIVE_SESSIONS,
        REQUESTS_TOTAL,
        TOKEN_INVALIDATIONS_TOTAL,
        TOKEN_ISSUES_TOTAL,
        metrics_app,
        track_request,
    )
    assert metrics_app is not None
    assert callable(track_request)


def test_track_request_increments_counter():
    """track_request() increments the success counter on normal exit."""
    from raas_mcp.metrics import REQUESTS_TOTAL, track_request

    before = REQUESTS_TOTAL.labels(status="success")._value.get()
    with track_request():
        pass
    after = REQUESTS_TOTAL.labels(status="success")._value.get()
    assert after == before + 1


def test_track_request_error_path_increments_error_counter():
    """track_request() increments the error counter when an exception is raised."""
    from raas_mcp.metrics import REQUESTS_TOTAL, track_request

    before = REQUESTS_TOTAL.labels(status="error")._value.get()
    with pytest.raises(RuntimeError):
        with track_request():
            raise RuntimeError("boom")
    after = REQUESTS_TOTAL.labels(status="error")._value.get()
    assert after == before + 1


def test_active_sessions_gauge_resets_after_context():
    """ACTIVE_SESSIONS gauge returns to its prior value after context exits."""
    from raas_mcp.metrics import ACTIVE_SESSIONS, track_request

    baseline = ACTIVE_SESSIONS._value.get()
    with track_request():
        in_context = ACTIVE_SESSIONS._value.get()
    after = ACTIVE_SESSIONS._value.get()
    assert in_context == baseline + 1
    assert after == baseline
