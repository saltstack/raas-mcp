"""Unit tests for raas_mcp.raas_client — the vendored httpx-based RaaS client.

Covers the login flow (XSRF cookie + JWT), RPC dispatch, the /rpc → /raas/rpc
fallback, one-shot 401 re-auth, and the VIDB JWT passthrough path (including
a regression test proving the previously-dropped ``auth_token`` mapping key
now actually reaches RaaS as a Bearer header).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from raas_mcp.raas_client import RaasApiError, RaasClient, connect_from_mapping

SERVER = "http://raas.test"


def _set_cookie(name: str, value: str) -> list[tuple[str, str]]:
    return [("set-cookie", f"{name}={value}; Path=/")]


def _login_route(router: respx.MockRouter, *, jwt: str = "fake-jwt") -> None:
    router.get(f"{SERVER}/account/login").mock(
        return_value=httpx.Response(200, headers=_set_cookie("_xsrf", "xsrf-token-1"))
    )
    router.post(f"{SERVER}/account/login").mock(
        return_value=httpx.Response(
            200, json={"jwt": jwt}, headers=_set_cookie("_xsrf", "xsrf-token-2")
        )
    )


# ---------------------------------------------------------------------------
# Login + RPC success
# ---------------------------------------------------------------------------


@respx.mock
def test_login_and_rpc_call_success():
    _login_route(respx)
    rpc = respx.post(f"{SERVER}/rpc").mock(
        return_value=httpx.Response(200, json={"riq": 1, "ret": ["minion-01"], "error": None})
    )

    client = RaasClient.login(SERVER, "alice", "pw")
    result = client.call("ret", "get_minions")

    assert result == ["minion-01"]
    sent = rpc.calls.last.request
    assert sent.headers["Authorization"] == "JWT fake-jwt"
    assert sent.headers["X-Xsrftoken"] == "xsrf-token-2"
    import json as _json
    body = _json.loads(sent.content)
    assert body["resource"] == "ret"
    assert body["method"] == "get_minions"


@respx.mock
def test_api_proxy_dynamic_attribute_access():
    """client.api.<resource>.<method>(**kwargs) — dispatcher.py's call shape."""
    _login_route(respx)
    respx.post(f"{SERVER}/rpc").mock(
        return_value=httpx.Response(200, json={"riq": 1, "ret": {"cmd_id": "abc"}, "error": None})
    )

    client = RaasClient.login(SERVER, "alice", "pw")
    result = client.api.cmd.route_cmd(tgt="*", fun="test.ping")

    assert result == {"cmd_id": "abc"}


@respx.mock
def test_rpc_kwargs_forwarded_as_kwarg_payload():
    _login_route(respx)
    rpc = respx.post(f"{SERVER}/rpc").mock(
        return_value=httpx.Response(200, json={"riq": 1, "ret": "ok", "error": None})
    )

    client = RaasClient.login(SERVER, "alice", "pw")
    client.call("cmd", "route_cmd", tgt="*", fun="test.ping")

    import json as _json
    body = _json.loads(rpc.calls.last.request.content)
    assert body["kwarg"] == {"tgt": "*", "fun": "test.ping"}


# ---------------------------------------------------------------------------
# RPC error payload
# ---------------------------------------------------------------------------


@respx.mock
def test_rpc_error_payload_raises():
    _login_route(respx)
    respx.post(f"{SERVER}/rpc").mock(
        return_value=httpx.Response(
            200, json={"riq": 1, "ret": None, "error": {"message": "no such minion"}}
        )
    )

    client = RaasClient.login(SERVER, "alice", "pw")
    with pytest.raises(RaasApiError, match="no such minion"):
        client.call("ret", "get_minions")


# ---------------------------------------------------------------------------
# /rpc -> /raas/rpc fallback
# ---------------------------------------------------------------------------


@respx.mock
def test_rpc_path_falls_back_to_raas_rpc_on_404():
    _login_route(respx)
    respx.post(f"{SERVER}/rpc").mock(return_value=httpx.Response(404))
    fallback = respx.post(f"{SERVER}/raas/rpc").mock(
        return_value=httpx.Response(200, json={"riq": 1, "ret": ["m1"], "error": None})
    )

    client = RaasClient.login(SERVER, "alice", "pw")
    result = client.call("ret", "get_minions")

    assert result == ["m1"]
    assert fallback.called


# ---------------------------------------------------------------------------
# One-shot 401 re-auth then retry
# ---------------------------------------------------------------------------


@respx.mock
def test_401_triggers_one_shot_reauth_then_retry():
    respx.get(f"{SERVER}/account/login").mock(
        side_effect=[
            httpx.Response(200, headers=_set_cookie("_xsrf", "xsrf-1")),
            httpx.Response(200, headers=_set_cookie("_xsrf", "xsrf-2")),
        ]
    )
    respx.post(f"{SERVER}/account/login").mock(
        side_effect=[
            httpx.Response(200, json={"jwt": "jwt-1"}),
            httpx.Response(200, json={"jwt": "jwt-2"}),
        ]
    )
    respx.post(f"{SERVER}/rpc").mock(
        side_effect=[
            httpx.Response(401),
            httpx.Response(200, json={"riq": 1, "ret": ["m1"], "error": None}),
        ]
    )

    client = RaasClient.login(SERVER, "alice", "pw")
    result = client.call("ret", "get_minions")

    assert result == ["m1"]


@respx.mock
def test_401_twice_raises_with_unauthorized_in_message():
    """Second consecutive 401 must raise with '401'/'unauthorized' in the message
    so dispatcher.py's substring-based FR-010 invalidation logic still fires."""
    _login_route(respx)
    respx.post(f"{SERVER}/rpc").mock(return_value=httpx.Response(401))

    client = RaasClient.login(SERVER, "alice", "pw")
    with pytest.raises(RaasApiError, match="401"):
        client.call("ret", "get_minions")


# ---------------------------------------------------------------------------
# VIDB JWT passthrough (from_bearer) — the bug-fix regression test
# ---------------------------------------------------------------------------


@respx.mock
def test_from_bearer_forwards_jwt_unchanged_no_login():
    login_route = respx.get(f"{SERVER}/account/login")
    rpc = respx.post(f"{SERVER}/rpc").mock(
        return_value=httpx.Response(200, json={"riq": 1, "ret": ["m1"], "error": None})
    )

    client = RaasClient.from_bearer(SERVER, "vidb.jwt.token")
    result = client.call("ret", "get_minions")

    assert result == ["m1"]
    assert not login_route.called, "from_bearer must skip the login flow entirely"
    sent = rpc.calls.last.request
    assert sent.headers["Authorization"] == "Bearer vidb.jwt.token"


@respx.mock
def test_from_bearer_401_does_not_reauth():
    respx.post(f"{SERVER}/rpc").mock(return_value=httpx.Response(401))

    client = RaasClient.from_bearer(SERVER, "vidb.jwt.token")
    with pytest.raises(RaasApiError, match="401"):
        client.call("ret", "get_minions")


@respx.mock
def test_connect_from_mapping_auth_token_regression():
    """Regression test: connect_from_mapping({"auth_token": jwt}) must actually
    reach RaaS as a Bearer header — this was silently dropped by an earlier
    internal implementation of this RaaS client wrapper."""
    rpc = respx.post(f"{SERVER}/rpc").mock(
        return_value=httpx.Response(200, json={"riq": 1, "ret": "ok", "error": None})
    )

    client = connect_from_mapping({"raas": SERVER, "auth_token": "my.vidb.jwt"})
    client.call("api", "get_versions")

    sent = rpc.calls.last.request
    assert sent.headers["Authorization"] == "Bearer my.vidb.jwt"


@respx.mock
def test_connect_from_mapping_auth_login_path():
    _login_route(respx)
    respx.post(f"{SERVER}/rpc").mock(
        return_value=httpx.Response(200, json={"riq": 1, "ret": "ok", "error": None})
    )

    client = connect_from_mapping({"raas": SERVER, "auth": "alice:pw"})
    result = client.call("api", "get_versions")

    assert result == "ok"


def test_connect_from_mapping_requires_auth_or_auth_token():
    with pytest.raises(RaasApiError, match="auth"):
        connect_from_mapping({"raas": SERVER})


# ---------------------------------------------------------------------------
# Login failure surfaces as RaasApiError
# ---------------------------------------------------------------------------


@respx.mock
def test_login_invalid_credentials_raises():
    respx.get(f"{SERVER}/account/login").mock(return_value=httpx.Response(200))
    respx.post(f"{SERVER}/account/login").mock(return_value=httpx.Response(401))

    with pytest.raises(RaasApiError, match="401"):
        RaasClient.login(SERVER, "alice", "wrong-password")
