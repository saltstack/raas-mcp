"""Minimal RaaS RPC client on top of ``httpx`` — no SSEApiClient dependency.

A small, self-contained implementation of the RaaS wire protocol, so
raas-mcp has no runtime dependency on any non-public package.

Wire protocol (mirrors the RaaS/Aria Automation Config "SSC" auth flow, the
same one implemented independently in the public ``raas-cli`` project's
``AriaConfigClient``):

1. ``GET  {server}/account/login``  — obtain an ``_xsrf`` cookie.
2. ``POST {server}/account/login``  — body ``{username, password,
   config_name, token_type: "jwt"}`` — returns a JWT in the response body.
3. Every RPC call is ``POST {server}/rpc`` (falling back to the legacy
   ``/raas/rpc`` path on 404/405) with body ``{"resource", "method", "arg",
   "kwarg", "riq"}`` and headers ``Authorization: JWT <jwt>`` +
   ``X-Xsrftoken: <xsrf>``.
4. On ``401`` the client re-authenticates once and retries.

VIDB JWT passthrough (OIDC SSO) skips the login flow entirely: the caller's
own JWT is forwarded unchanged as ``Authorization: Bearer <jwt>`` on every
RPC call. RaaS validates it directly — see spec 010's VIDB integration.

Public surface
--------------
``connect_from_mapping(mapping)`` is the single entry point used by
``dispatcher.py`` / ``auth/token_endpoint.py`` — those modules only import
the function, they never construct :class:`RaasClient` directly.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx


class RaasApiError(Exception):
    """Raised for any RaaS RPC failure (HTTP error, RPC error payload, or
    connection issue). ``str(exc)`` includes the literal status code / one of
    "unauthorized"/"forbidden" when applicable, so callers that pattern-match
    on those substrings (see ``dispatcher.py``'s FR-010 token-invalidation
    check) keep working unmodified."""


class RaasClient:
    """RaaS RPC client. Construct via :meth:`login` (username/password) or
    :meth:`from_bearer` (VIDB JWT passthrough) — not directly."""

    _RPC_PATHS = ("/rpc", "/raas/rpc")
    _LOGIN_PATHS = ("/account/login", "/raas/account/login")

    def __init__(
        self,
        server: str,
        *,
        timeout: float = 60.0,
        insecure: bool = False,
    ) -> None:
        self._server = server.rstrip("/")
        self._timeout = timeout
        self._client = httpx.Client(timeout=timeout, verify=not insecure, follow_redirects=True)
        self._xsrf_token: str | None = None
        self._jwt: str | None = None
        self._bearer_token: str | None = None  # VIDB JWT passthrough
        self._riq = 0
        # Login-flow credentials, retained for one-shot re-auth on 401.
        self._username: str | None = None
        self._password: str | None = None
        self._config_name: str = "internal"
        self._rpc_path = self._RPC_PATHS[0]
        self._login_path = self._LOGIN_PATHS[0]
        self._api = _ApiProxy(self)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def login(
        cls,
        server: str,
        username: str,
        password: str,
        *,
        config_name: str = "internal",
        timeout: float = 60.0,
        insecure: bool = False,
    ) -> RaasClient:
        """Build a client that authenticates via the RaaS username/password flow."""
        client = cls(server, timeout=timeout, insecure=insecure)
        client._username = username
        client._password = password
        client._config_name = config_name
        client._authenticate()
        return client

    @classmethod
    def from_bearer(
        cls,
        server: str,
        token: str,
        *,
        timeout: float = 60.0,
        insecure: bool = False,
    ) -> RaasClient:
        """Build a client that forwards *token* unchanged as ``Authorization:
        Bearer <token>`` on every RPC call (VIDB JWT / OIDC SSO passthrough).
        No login flow is performed."""
        client = cls(server, timeout=timeout, insecure=insecure)
        client._bearer_token = token
        return client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def api(self) -> _ApiProxy:
        """``client.api.<resource>.<method>(**kwargs)`` — matches the
        dynamic-attribute interface the old SSEApiClient-backed client
        exposed, so dispatcher.py / token_endpoint.py need no changes."""
        return self._api

    def call(self, resource: str, method: str, **kwargs: Any) -> Any:
        """Invoke ``resource.method(**kwargs)`` over RaaS RPC.

        Returns the ``ret`` value on success. Raises :class:`RaasApiError`
        on any RPC-level error or unrecoverable HTTP failure.
        """
        self._riq += 1
        payload: dict[str, Any] = {"resource": resource, "method": method, "riq": self._riq}
        if kwargs:
            payload["kwarg"] = kwargs

        response = self._post_rpc(payload)

        if response.status_code == 401:
            if self._bearer_token is not None:
                # VIDB passthrough — lifecycle managed by VIDB, no local re-auth.
                raise RaasApiError(f"401 Unauthorized calling {resource}.{method}")
            # One-shot re-auth then retry, mirroring AriaConfigClient.
            self._jwt = None
            self._authenticate()
            response = self._post_rpc(payload)
            if response.status_code == 401:
                raise RaasApiError(f"401 Unauthorized calling {resource}.{method}")

        if response.status_code == 403:
            raise RaasApiError(f"403 Forbidden calling {resource}.{method}")
        if response.status_code >= 500:
            raise RaasApiError(
                f"RaaS server error {response.status_code} calling {resource}.{method}"
            )
        if response.status_code >= 400:
            raise RaasApiError(f"RaaS RPC error {response.status_code} calling {resource}.{method}")

        try:
            data = response.json()
        except ValueError as exc:
            raise RaasApiError(f"Invalid JSON response calling {resource}.{method}: {exc}") from exc

        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            message = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            raise RaasApiError(f"RaaS error calling {resource}.{method}: {message}")

        return data.get("ret") if isinstance(data, dict) else data

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._xsrf_token:
            headers["X-Xsrftoken"] = self._xsrf_token
        if self._bearer_token:
            headers["Authorization"] = f"Bearer {self._bearer_token}"
        elif self._jwt:
            headers["Authorization"] = f"JWT {self._jwt}"
        return headers

    def _post_rpc(self, payload: dict[str, Any]) -> httpx.Response:
        try:
            response = self._client.post(
                f"{self._server}{self._rpc_path}",
                headers=self._headers(),
                json=payload,
                timeout=self._timeout,
            )
        except httpx.RequestError as exc:
            raise RaasApiError(f"Connection error calling RaaS: {exc}") from exc

        if response.status_code in (404, 405):
            alt = self._other(self._RPC_PATHS, self._rpc_path)
            if alt is not None:
                self._rpc_path = alt
                try:
                    response = self._client.post(
                        f"{self._server}{self._rpc_path}",
                        headers=self._headers(),
                        json=payload,
                        timeout=self._timeout,
                    )
                except httpx.RequestError as exc:
                    raise RaasApiError(f"Connection error calling RaaS: {exc}") from exc
        return response

    def _authenticate(self) -> None:
        """Username/password login flow: XSRF cookie → POST login → JWT."""
        if self._username is None or self._password is None:
            raise RaasApiError("RaasClient.login() credentials are required to authenticate")

        try:
            resp = self._client.get(f"{self._server}{self._login_path}")
        except httpx.RequestError as exc:
            raise RaasApiError(f"Cannot reach RaaS server at {self._server}: {exc}") from exc
        if resp.status_code in (404, 405):
            alt = self._other(self._LOGIN_PATHS, self._login_path)
            if alt is not None:
                self._login_path = alt
                try:
                    resp = self._client.get(f"{self._server}{self._login_path}")
                except httpx.RequestError as exc:
                    raise RaasApiError(
                        f"Cannot reach RaaS server at {self._server}: {exc}"
                    ) from exc
        self._extract_xsrf(resp)

        body = {
            "username": self._username,
            "password": self._password,
            "config_name": self._config_name,
            "token_type": "jwt",
        }
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._xsrf_token:
            headers["X-Xsrftoken"] = self._xsrf_token
        try:
            resp = self._client.post(
                f"{self._server}{self._login_path}",
                headers=headers,
                json=body,
                timeout=self._timeout,
            )
        except httpx.RequestError as exc:
            raise RaasApiError(f"Login request failed: {exc}") from exc
        self._extract_xsrf(resp)

        if resp.status_code in (401, 403):
            raise RaasApiError("401 Unauthorized: invalid RaaS username or password")
        if resp.status_code >= 500:
            raise RaasApiError(f"RaaS server error {resp.status_code} during login")

        try:
            data = resp.json()
        except ValueError as exc:
            raise RaasApiError(f"Login response was not valid JSON: {exc}") from exc

        jwt_token = None
        if isinstance(data, dict):
            jwt_token = data.get("jwt")
            if not jwt_token and isinstance(data.get("data"), dict):
                jwt_token = data["data"].get("jwt")
        if not jwt_token:
            raise RaasApiError("401 Unauthorized: RaaS login did not return a JWT")
        self._jwt = jwt_token

    def _extract_xsrf(self, response: httpx.Response) -> None:
        for name, value in response.cookies.items():
            if name == "_xsrf":
                self._xsrf_token = value
                return
        jar_value = self._client.cookies.get("_xsrf")
        if jar_value:
            self._xsrf_token = jar_value

    @staticmethod
    def _other(candidates: tuple[str, ...], current: str) -> str | None:
        for candidate in candidates:
            if candidate != current:
                return candidate
        return None


class _MethodProxy:
    """Callable wrapper: ``resource_proxy.method(**kwargs)`` →
    ``client.call(resource, method, **kwargs)``."""

    def __init__(self, client: RaasClient, resource: str, method: str) -> None:
        self._client = client
        self._resource = resource
        self._method = method

    def __call__(self, **kwargs: Any) -> Any:
        return self._client.call(self._resource, self._method, **kwargs)


class _ResourceProxy:
    """``client.api.<resource>`` — attribute access returns a :class:`_MethodProxy`."""

    def __init__(self, client: RaasClient, resource: str) -> None:
        self._client = client
        self._resource = resource

    def __getattr__(self, method: str) -> _MethodProxy:
        return _MethodProxy(self._client, self._resource, method)


class _ApiProxy:
    """``client.api`` — attribute access returns a :class:`_ResourceProxy`."""

    def __init__(self, client: RaasClient) -> None:
        self._client = client

    def __getattr__(self, resource: str) -> _ResourceProxy:
        return _ResourceProxy(self._client, resource)


def connect_from_mapping(m: Mapping[str, Any]) -> RaasClient:
    """Build a :class:`RaasClient` from a mapping, matching the shape
    ``dispatcher.py`` / ``auth/token_endpoint.py`` already construct:

    - ``{"raas": url, "auth_token": jwt, "timeout": ..., "insecure": ...}``
      → VIDB JWT passthrough (:meth:`RaasClient.from_bearer`).
    - ``{"raas": url, "auth": "user:pass", "config_name": ..., "timeout": ...,
      "insecure": ...}`` → username/password login flow (:meth:`RaasClient.login`).

    Note: an earlier internal implementation of this RaaS client wrapper had
    a latent bug where an ``auth_token`` mapping key was silently dropped
    (never forwarded to RaaS) — the VIDB JWT passthrough path never actually
    worked. This implementation fixes that; see ``tests/test_raas_client.py``
    for the regression test.
    """
    server = str(m.get("raas") or "")
    timeout = float(m.get("timeout", 60.0))
    insecure = bool(m.get("insecure", False))

    auth_token = m.get("auth_token")
    if auth_token:
        return RaasClient.from_bearer(server, str(auth_token), timeout=timeout, insecure=insecure)

    auth = m.get("auth")
    if not auth:
        raise RaasApiError(
            "connect_from_mapping: either 'auth' (user:pass) or 'auth_token' is required"
        )
    parts = str(auth).split(":", 1)
    if len(parts) != 2 or not parts[0]:
        raise RaasApiError("connect_from_mapping: 'auth' must be 'USER:PASS'")
    username, password = parts
    config_name = str(m.get("config_name") or "internal")
    return RaasClient.login(
        server, username, password, config_name=config_name, timeout=timeout, insecure=insecure
    )
