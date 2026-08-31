"""Operator configuration for raas-mcp-server.

Reads credentials via ``raas_mcp.config_file`` (same precedence chain and
``~/.salt/config.yml`` path as ``vcf-salt``, so both tools can share one
config file) and reads the two MCP-specific keys ``allowed_tools`` and
``approval_gate`` directly from the raw YAML (bypassing ``_normalize`` which
drops unknown keys).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ServerConfig:
    raas_url: str
    auth: str | None
    config_name: str
    timeout: float
    insecure: bool
    allowed_tools: list[str] | None
    approval_gate: list[str] = field(default_factory=list)


def load() -> ServerConfig:
    """Load server configuration from ``~/.salt/config.yml`` and env vars."""
    from raas_mcp.config_file import (
        _load_raw,
        config_path,
        resolve_auth,
        resolve_config_name,
        resolve_insecure,
        resolve_raas,
        resolve_timeout,
    )

    raw = _load_raw(config_path())

    allowed_tools_raw = raw.get("allowed_tools")
    if allowed_tools_raw is None:
        allowed_tools: list[str] | None = None
    elif isinstance(allowed_tools_raw, list):
        allowed_tools = [str(x) for x in allowed_tools_raw if x]
    else:
        allowed_tools = None

    gate_raw = raw.get("approval_gate")
    if isinstance(gate_raw, list):
        approval_gate: list[str] = [str(x) for x in gate_raw if x]
    else:
        approval_gate = []

    return ServerConfig(
        raas_url=resolve_raas(None),
        auth=resolve_auth(None),
        config_name=resolve_config_name(None),
        timeout=resolve_timeout(None),
        insecure=resolve_insecure(False),
        allowed_tools=allowed_tools,
        approval_gate=approval_gate,
    )


def _matches(tool_name: str, pattern: str) -> bool:
    """Return True if tool_name matches an exact name or a ``prefix_*`` glob."""
    if pattern.endswith("_*"):
        prefix = pattern[:-1]  # "ret_*" → "ret_"
        return tool_name.startswith(prefix)
    return tool_name == pattern


def tool_is_allowed(tool_name: str, allowed: list[str] | None) -> bool:
    """Return True if the tool is permitted by the allowlist.

    ``None`` means all tools are allowed.  An empty list means no tools.
    """
    if allowed is None:
        return True
    return any(_matches(tool_name, pattern) for pattern in allowed)


def tool_is_gated(tool_name: str, gate: list[str]) -> bool:
    """Return True if the tool requires approval-gate confirmation."""
    return any(_matches(tool_name, pattern) for pattern in gate)
