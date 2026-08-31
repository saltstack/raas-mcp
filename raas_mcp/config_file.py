"""Operator defaults from ``~/.salt/config.yml`` (YAML).

Follows the same ``~/.salt/`` dotdir convention used across the Salt
ecosystem. Provides ``resolve_raas``/``resolve_auth``/``resolve_config_name``/
``resolve_timeout``/``resolve_insecure``/``config_path``/``_load_raw``, with
CLI value > environment variable (``RAASS_*``/``RAAS_*``) > config-file
precedence. The path can be overridden with ``RAAS_MCP_CONFIG``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

# Override path with RAAS_MCP_CONFIG (absolute or ~-expanded).
_DEFAULT_PATH = Path.home() / ".salt" / "config.yml"

_cache_key: tuple[str | None, float | None] | None = None
_cache_cfg: dict[str, Any] | None = None


def config_path() -> Path:
    override = os.environ.get("RAAS_MCP_CONFIG")
    if override:
        return Path(override).expanduser()
    return _DEFAULT_PATH


def invalidate_cache() -> None:
    """Test hook: force next read to reload from disk."""
    global _cache_key, _cache_cfg
    _cache_key = None
    _cache_cfg = None


def _load_raw(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise SystemExit(f"Invalid YAML in {path}: {e}") from e
    except OSError as e:
        raise SystemExit(f"Cannot read {path}: {e}") from e
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise SystemExit(f"{path} must be a YAML mapping (object) at the top level.")
    return data


def _normalize(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    raas = raw.get("raas_url") or raw.get("url") or raw.get("raas")
    if raas is not None and str(raas).strip():
        out["raas_url"] = str(raas).strip()

    auth = raw.get("auth")
    if auth is not None and str(auth).strip():
        out["auth"] = str(auth).strip()
    else:
        u, pw = raw.get("username"), raw.get("password")
        if u is not None and pw is not None and str(u).strip() and str(pw).strip():
            out["auth"] = f"{str(u).strip()}:{str(pw).strip()}"

    cn = raw.get("config_name")
    if cn is not None and str(cn).strip():
        out["config_name"] = str(cn).strip()

    if raw.get("timeout") is not None:
        out["timeout"] = float(raw["timeout"])

    if "insecure" in raw and raw["insecure"] is not None:
        out["insecure"] = bool(raw["insecure"])
    elif "tls_verify" in raw and raw["tls_verify"] is not None:
        out["insecure"] = not bool(raw["tls_verify"])

    return out


def get_merged_user_config() -> dict[str, Any]:
    """Return normalized defaults from the config file (empty dict if missing)."""
    global _cache_key, _cache_cfg
    path = config_path()
    try:
        mtime = path.stat().st_mtime if path.is_file() else None
    except OSError:
        mtime = None
    key = (str(path), mtime)
    if _cache_key == key and _cache_cfg is not None:
        return _cache_cfg
    raw = _load_raw(path)
    _cache_cfg = _normalize(raw)
    _cache_key = key
    return _cache_cfg


def resolve_raas(cli_value: str | None) -> str:
    """CLI value wins, then env, then config ``raas_url`` / ``url`` / ``raas``."""
    if cli_value and str(cli_value).strip():
        return str(cli_value).strip()
    cfg = get_merged_user_config()
    return (
        os.environ.get("RAASS_URL")
        or os.environ.get("SSE_RAAS_URL")
        or cfg.get("raas_url")
        or "http://localhost:8080"
    )


def resolve_auth(cli_value: str | None) -> str | None:
    """CLI value wins, then env, then config ``auth`` or ``username``+``password``."""
    if cli_value and str(cli_value).strip():
        return str(cli_value).strip()
    cfg = get_merged_user_config()
    return os.environ.get("RAASS_AUTH") or os.environ.get("SSE_RAAS_AUTH") or cfg.get("auth")


def resolve_config_name(cli_value: str | None) -> str:
    if cli_value and str(cli_value).strip():
        return str(cli_value).strip()
    cfg = get_merged_user_config()
    return os.environ.get("RAASS_CONFIG_NAME") or cfg.get("config_name") or "internal"


def resolve_timeout(cli_value: float | None) -> float:
    if cli_value is not None:
        return float(cli_value)
    cfg = get_merged_user_config()
    env_t = os.environ.get("SSE_TIMEOUT")
    if env_t:
        return float(env_t)
    if "timeout" in cfg:
        return float(cfg["timeout"])
    return 120.0


def resolve_insecure(cli_flag: bool) -> bool:
    """True if ``--insecure`` was passed, or config sets
    ``insecure: true`` / ``tls_verify: false``."""
    if cli_flag:
        return True
    cfg = get_merged_user_config()
    return bool(cfg.get("insecure", False))
