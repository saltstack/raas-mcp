"""Dispatch MCP tool calls to RaaS via sseapiclient.

Validates parameters strictly, checks the approval gate (FR-015),
dispatches to RaaS, and returns a structured result (FR-006/FR-007).

HTTP-transport path — opaque token
-----------------------------------
When called from the HTTP server with ``raas_user`` + ``raas_password``,
a per-request RaaS client is built using HTTP Basic Auth.
On RaaS 401/403 the opaque bearer token is immediately invalidated (FR-010).

HTTP-transport path — VIDB JWT
--------------------------------
When called with ``vidb_jwt`` set, the per-request RaaS client is built
using ``Authorization: Bearer <vidb_jwt>`` instead of Basic Auth.
The token is NOT invalidated in the local store on RaaS auth failure —
its lifecycle is managed by VIDB.

stdio path
----------
When ``client`` is supplied directly (stdio mode), the per-request
credential arguments are unused and the shared client is used as before.
"""

from __future__ import annotations

import socket
from typing import Any

from mcp import types as mcp_types

from raas_mcp.catalog import CatalogEntry
from raas_mcp.errors import ErrorCode, error_result, success_result
from raas_mcp.server_config import tool_is_gated

_SENTINEL = object()

try:
    from vcf_salt.connection import connect_from_mapping  # type: ignore[import-untyped]
except ImportError:
    connect_from_mapping = None  # type: ignore[assignment]


async def dispatch(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    client: Any = _SENTINEL,
    catalog_entries: dict[str, CatalogEntry],
    approval_gate: list[str],
    ctx: Any,
    # HTTP-transport per-request credentials (opaque path)
    raas_user: str | None = None,
    raas_password: str | None = None,
    bearer_token: str | None = None,
    raas_url: str | None = None,
    raas_insecure: bool = False,
    raas_timeout: float = 60.0,
    token_store: Any | None = None,
    # HTTP-transport VIDB JWT path (mutually exclusive with raas_user/raas_password)
    vidb_jwt: str | None = None,
) -> list[mcp_types.TextContent]:
    """Validate, gate-check, and dispatch a tool call to RaaS.

    Returns a list containing exactly one TextContent whose ``.text`` is a
    JSON object matching ``contracts/mcp-tool-result.schema.json``.
    """
    # Resolve which client to use
    if client is _SENTINEL:
        if connect_from_mapping is None:
            return error_result(
                ErrorCode.RAAS_RPC_ERROR,
                "vcf_salt is not installed; HTTP transport mode requires the vendor wheel.",
            )

        if vidb_jwt:
            # VIDB JWT path: forward the JWT as a Bearer token to RaaS
            if not raas_url:
                return error_result(
                    ErrorCode.RAAS_RPC_ERROR,
                    "HTTP transport (VIDB path): raas_url is required.",
                )
            try:
                resolved_client = connect_from_mapping(
                    {
                        "raas": raas_url,
                        "auth_token": vidb_jwt,
                        "timeout": raas_timeout,
                        "insecure": raas_insecure,
                    }
                )
            except Exception as exc:
                return error_result(
                    ErrorCode.RAAS_NETWORK_ERROR,
                    _redact(f"Failed to connect to RaaS (VIDB path): {exc}"),
                )
        else:
            # Opaque token path: Basic Auth
            if not (raas_user and raas_password and raas_url):
                return error_result(
                    ErrorCode.RAAS_RPC_ERROR,
                    "HTTP transport: raas_user, raas_password, and raas_url are required "
                    "when no shared client is provided.",
                )
            try:
                resolved_client = connect_from_mapping(
                    {
                        "raas": raas_url,
                        "auth": f"{raas_user}:{raas_password}",
                        "timeout": raas_timeout,
                        "insecure": raas_insecure,
                    }
                )
            except Exception as exc:
                return error_result(
                    ErrorCode.RAAS_NETWORK_ERROR,
                    _redact(f"Failed to connect to RaaS: {exc}"),
                )
    else:
        resolved_client = client

    # 1. Look up tool in catalog
    entry = catalog_entries.get(tool_name)
    if entry is None:
        return error_result(
            ErrorCode.UNKNOWN_TOOL,
            f"Tool '{tool_name}' is not in the exposed catalog. "
            "Check the allowed_tools configuration or verify the tool name.",
        )

    # 2. Strict parameter validation — extra keys
    extra_keys = sorted(set(arguments.keys()) - entry.known_params)
    if extra_keys:
        return error_result(
            ErrorCode.VALIDATION_ERROR,
            f"Tool '{tool_name}' received unrecognized parameters: {extra_keys}. "
            "No RaaS call was dispatched.",
            details={"extra_keys": extra_keys, "missing_keys": []},
        )

    # 3. Strict parameter validation — missing required keys
    missing_keys = sorted(entry.required_params - set(arguments.keys()))
    if missing_keys:
        return error_result(
            ErrorCode.VALIDATION_ERROR,
            f"Tool '{tool_name}' is missing required parameters: {missing_keys}. "
            "No RaaS call was dispatched.",
            details={"extra_keys": [], "missing_keys": missing_keys},
        )

    # 4. Approval gate check
    if tool_is_gated(tool_name, approval_gate):
        gate_result = await _check_approval_gate(tool_name, arguments, ctx=ctx)
        if gate_result is not None:
            return gate_result

    # 5. Dispatch to RaaS
    try:
        resource_obj = getattr(resolved_client.api, entry.resource)
        method_fn = getattr(resource_obj, entry.method)
        ret = method_fn(**arguments)
        return success_result(entry.resource, entry.method, ret)
    except (ConnectionError, OSError, socket.error) as exc:
        return error_result(
            ErrorCode.RAAS_NETWORK_ERROR,
            _redact(f"Network error calling {entry.resource}.{entry.method}: {exc}"),
        )
    except Exception as exc:
        exc_str = str(exc)
        # FR-010: on mid-call RaaS 401/403, immediately invalidate the opaque bearer
        # token. Do NOT invalidate for the VIDB JWT path — the token lifecycle is
        # managed by VIDB, not the local token store.
        if bearer_token and token_store is not None and not vidb_jwt:
            low = exc_str.lower()
            if "401" in low or "403" in low or "unauthorized" in low or "forbidden" in low:
                token_store.invalidate(bearer_token)
                try:
                    from raas_mcp.metrics import TOKEN_INVALIDATIONS_TOTAL
                    TOKEN_INVALIDATIONS_TOTAL.inc()
                except Exception:
                    pass
        return error_result(
            ErrorCode.RAAS_RPC_ERROR,
            _redact(f"RaaS error calling {entry.resource}.{entry.method}: {exc_str}"),
        )


async def _check_approval_gate(
    tool_name: str, arguments: dict[str, Any], *, ctx: Any
) -> list[mcp_types.TextContent] | None:
    """Request operator confirmation.  Returns an error result if denied/unsupported, else None."""
    # Try MCP elicitation if the context supports it
    if ctx is not None and hasattr(ctx, "request_elicitation"):
        try:
            result = await ctx.request_elicitation(
                message=(
                    f"Tool '{tool_name}' is in the approval gate. "
                    f"Arguments: {arguments}. Approve this RaaS call?"
                ),
                requested_schema={
                    "type": "object",
                    "properties": {"approve": {"type": "boolean"}},
                    "required": ["approve"],
                },
            )
            if result and result.get("approve"):
                return None  # approved — proceed
            return error_result(
                ErrorCode.APPROVAL_DENIED,
                f"Operator declined the call to '{tool_name}'. No RaaS action was taken.",
            )
        except Exception:
            pass  # fall through to unsupported-elicitation path

    # Elicitation not available — return a clear instructional error
    return error_result(
        ErrorCode.APPROVAL_REQUIRED,
        f"Tool '{tool_name}' is in the approval_gate list and requires operator confirmation. "
        "Your AI framework does not support interactive approval (MCP elicitation). "
        "To allow unapproved calls, remove this tool from approval_gate in "
        "~/.salt/config.yml. To enable interactive confirmation, use an AI framework "
        "that supports MCP elicitation (Cursor v0.48+, Claude Desktop).",
    )


def _redact(message: str) -> str:
    """Best-effort removal of credentials from error messages."""
    try:
        from vcf_salt.redact import redact_secrets
        return redact_secrets(message)
    except Exception:
        return message
