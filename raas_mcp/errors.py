"""Structured error and success result builders for MCP tool call responses.

Every call_tool response body is a JSON object whose shape is defined in
``specs/008-raas-mcp-server/contracts/mcp-tool-result.schema.json``.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

from mcp import types


class ErrorCode(str, Enum):
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_DENIED = "APPROVAL_DENIED"
    RAAS_RPC_ERROR = "RAAS_RPC_ERROR"
    RAAS_NETWORK_ERROR = "RAAS_NETWORK_ERROR"
    CATALOG_UNAVAILABLE = "CATALOG_UNAVAILABLE"


def error_result(
    code: ErrorCode | str,
    message: str,
    details: dict[str, Any] | None = None,
) -> list[types.TextContent]:
    """Return an MCP TextContent list encoding a structured error payload."""
    body: dict[str, Any] = {
        "ok": False,
        "error": {
            "code": code.value if isinstance(code, ErrorCode) else str(code),
            "message": message,
        },
    }
    if details:
        body["error"]["details"] = details
    return [types.TextContent(type="text", text=json.dumps(body))]


def success_result(
    resource: str,
    method: str,
    result: Any,
) -> list[types.TextContent]:
    """Return an MCP TextContent list encoding a structured success payload."""
    body: dict[str, Any] = {
        "ok": True,
        "resource": resource,
        "method": method,
        "result": result,
    }
    return [types.TextContent(type="text", text=json.dumps(body))]
