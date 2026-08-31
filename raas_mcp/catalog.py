"""Build the MCP tool catalog from the bundled RaaS API discovery document.

Each resource/method pair in ``api_discovery.json`` becomes one
``CatalogEntry``.  The catalog is loaded once at startup and cached in the
module-level ``_CATALOG`` variable.  ``build_tool_list`` converts the cached
entries to ``mcp.types.Tool`` objects, optionally filtered by an allowlist.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

from mcp import types as mcp_types


# ---------------------------------------------------------------------------
# Internal helpers – allow tests to patch the discovery loader
# ---------------------------------------------------------------------------

def _load_raw_discovery() -> dict[str, Any] | None:
    """Return the raw API discovery dict or None if unavailable."""
    try:
        from raas_mcp.discovery import load_discovery_dict
        return load_discovery_dict()
    except Exception:
        return None


def _object_schema_from_detailed(detailed: dict[str, Any]) -> dict[str, Any] | None:
    """Extract the JSON Schema properties object from a method's detailed block."""
    schema = detailed.get("schema")
    if not isinstance(schema, dict):
        return None
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        parts = ref[2:].split("/")
        cur: Any = schema
        for key in parts:
            if not isinstance(cur, dict) or key not in cur:
                return None
            cur = cur[key]
        return cur if isinstance(cur, dict) else None
    if "properties" in schema or "required" in schema:
        return schema
    return schema if schema.get("type") == "object" else None


def _build_description(resource: str, method: str, minfo: dict[str, Any]) -> str:
    """Return a concise description, falling back to 'resource.method'."""
    try:
        from raas_mcp.help_text import build_rpc_command_help, is_generic_resource_doc
        _full, short = build_rpc_command_help(resource, method, minfo)
        if short and not is_generic_resource_doc(short):
            return short
    except Exception:
        pass
    formatted = (minfo.get("formatted") or "").strip()
    if formatted:
        try:
            from raas_mcp.help_text import is_generic_resource_doc
            if not is_generic_resource_doc(formatted):
                return formatted.splitlines()[0][:90]
        except Exception:
            return formatted.splitlines()[0][:90]
    detailed = minfo.get("detailed") or {}
    doc = (detailed.get("doc") or "").strip()
    if doc:
        try:
            from raas_mcp.help_text import is_generic_resource_doc
            if not is_generic_resource_doc(doc):
                return doc.splitlines()[0][:90]
        except Exception:
            return doc.splitlines()[0][:90]
    return f"{resource}.{method}"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CatalogEntry:
    tool_name: str
    resource: str
    method: str
    description: str
    input_schema: dict[str, Any]
    known_params: frozenset[str] = field(default_factory=frozenset)
    required_params: frozenset[str] = field(default_factory=frozenset)


# ---------------------------------------------------------------------------
# Catalog construction
# ---------------------------------------------------------------------------

_CATALOG: dict[str, CatalogEntry] | None = None


def _get_catalog() -> dict[str, CatalogEntry]:
    """Return the cached catalog, building it from the discovery doc if needed."""
    global _CATALOG
    if _CATALOG is not None:
        return _CATALOG

    raw = _load_raw_discovery()
    if not raw:
        sys.exit(
            "raas-mcp-server: CATALOG_UNAVAILABLE — api_discovery.json is missing or empty. "
            "Ensure raas_mcp/data/api_discovery.json exists in the installed package."
        )

    entries: dict[str, CatalogEntry] = {}

    for resource, body in raw.items():
        if not isinstance(body, dict):
            continue
        for method, minfo in body.items():
            if method == "__doc__" or not isinstance(minfo, dict):
                continue

            tool_name = f"{resource}_{method}"
            if tool_name in entries:
                sys.exit(
                    f"raas-mcp-server: tool name collision detected — "
                    f"'{tool_name}' is produced by both "
                    f"'{entries[tool_name].resource}.{entries[tool_name].method}' "
                    f"and '{resource}.{method}'. Cannot start with ambiguous catalog."
                )

            detailed = minfo.get("detailed") or {}
            schema_body = _object_schema_from_detailed(detailed) or {}
            props: dict[str, Any] = schema_body.get("properties") or {}
            req_list = schema_body.get("required") or []

            # Omit additionalProperties from the MCP Tool inputSchema so the
            # MCP SDK does not intercept validation — our dispatcher performs
            # strict parameter checking (FR-010) and returns structured errors.
            input_schema: dict[str, Any] = {
                "type": "object",
                "properties": props,
            }
            if req_list:
                input_schema["required"] = req_list

            entries[tool_name] = CatalogEntry(
                tool_name=tool_name,
                resource=resource,
                method=method,
                description=_build_description(resource, method, minfo),
                input_schema=input_schema,
                known_params=frozenset(props.keys()),
                required_params=frozenset(str(k) for k in req_list if isinstance(k, str)),
            )

    _CATALOG = entries
    return _CATALOG


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_tool_list(allowed: list[str] | None) -> list[mcp_types.Tool]:
    """Return MCP Tool objects for all (or allowed) catalog entries.

    When ``allowed`` is ``None`` all tools are returned.  When it is a list,
    only tools matching an exact name or a ``resource_*`` prefix glob are
    included.  Entries in ``allowed`` that match no catalog tool produce a
    startup warning on stderr.
    """
    from raas_mcp.server_config import tool_is_allowed

    catalog = _get_catalog()
    tools: list[mcp_types.Tool] = []

    if allowed is not None:
        import sys
        for pattern in allowed:
            if not any(tool_is_allowed(name, [pattern]) for name in catalog):
                print(
                    f"raas-mcp-server: WARNING — allowed_tools entry '{pattern}' "
                    "matches no catalog tool",
                    file=sys.stderr,
                )

    for entry in catalog.values():
        if not tool_is_allowed(entry.tool_name, allowed):
            continue
        tools.append(
            mcp_types.Tool(
                name=entry.tool_name,
                description=entry.description,
                inputSchema=entry.input_schema,
            )
        )

    return tools


def get_catalog_entries(allowed: list[str] | None) -> dict[str, CatalogEntry]:
    """Return the filtered CatalogEntry dict for dispatcher validation.

    Only entries matching the allowlist are returned, so the dispatcher
    correctly returns UNKNOWN_TOOL for tools excluded by the allowlist.
    """
    from raas_mcp.server_config import tool_is_allowed

    catalog = _get_catalog()
    if allowed is None:
        return catalog
    return {name: entry for name, entry in catalog.items() if tool_is_allowed(name, allowed)}
