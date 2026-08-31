"""Unit tests for raas_mcp.server_config — operator configuration loading."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml


def _write_config(tmp_path: Path, data: dict[str, Any]) -> Path:
    p = tmp_path / "config.yml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


class TestCredentialFields:
    def test_raas_url_from_config_file(self, tmp_path):
        cfg_path = _write_config(tmp_path, {"raas_url": "https://my-raas"})
        with patch.dict(os.environ, {"VCF_SALT_CONFIG": str(cfg_path)}, clear=False):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("RAASS_URL", None)
                os.environ.pop("SSE_RAAS_URL", None)
                from raas_mcp import server_config
                cfg = server_config.load()
                assert cfg.raas_url == "https://my-raas"

    def test_env_var_raass_url_takes_precedence(self, tmp_path):
        cfg_path = _write_config(tmp_path, {"raas_url": "https://file-raas"})
        env = {"VCF_SALT_CONFIG": str(cfg_path), "RAASS_URL": "https://env-raas"}
        with patch.dict(os.environ, env, clear=False):
            from raas_mcp import config_file as uc
            uc.invalidate_cache()
            from raas_mcp import server_config
            cfg = server_config.load()
            assert cfg.raas_url == "https://env-raas"

    def test_default_raas_url_when_no_config(self, tmp_path):
        nonexistent = str(tmp_path / "missing.yml")
        env = {"VCF_SALT_CONFIG": nonexistent}
        for key in ("RAASS_URL", "SSE_RAAS_URL"):
            env[key] = ""
        with patch.dict(os.environ, env, clear=False):
            from raas_mcp import config_file as uc
            uc.invalidate_cache()
            from raas_mcp import server_config
            cfg = server_config.load()
            assert cfg.raas_url  # non-empty (defaults to localhost or config default)


class TestMcpSpecificFields:
    def test_allowed_tools_null_by_default(self, tmp_path):
        cfg_path = _write_config(tmp_path, {"raas_url": "https://r"})
        with patch.dict(os.environ, {"VCF_SALT_CONFIG": str(cfg_path)}, clear=False):
            from raas_mcp import config_file as uc
            uc.invalidate_cache()
            from raas_mcp import server_config
            cfg = server_config.load()
            assert cfg.allowed_tools is None

    def test_allowed_tools_list_loaded(self, tmp_path):
        cfg_path = _write_config(
            tmp_path, {"raas_url": "https://r", "allowed_tools": ["ret_*", "tgt_*"]}
        )
        with patch.dict(os.environ, {"VCF_SALT_CONFIG": str(cfg_path)}, clear=False):
            from raas_mcp import config_file as uc
            uc.invalidate_cache()
            from raas_mcp import server_config
            cfg = server_config.load()
            assert cfg.allowed_tools == ["ret_*", "tgt_*"]

    def test_approval_gate_empty_by_default(self, tmp_path):
        cfg_path = _write_config(tmp_path, {"raas_url": "https://r"})
        with patch.dict(os.environ, {"VCF_SALT_CONFIG": str(cfg_path)}, clear=False):
            from raas_mcp import config_file as uc
            uc.invalidate_cache()
            from raas_mcp import server_config
            cfg = server_config.load()
            assert cfg.approval_gate == []

    def test_approval_gate_list_loaded(self, tmp_path):
        cfg_path = _write_config(
            tmp_path, {"raas_url": "https://r", "approval_gate": ["cmd_*"]}
        )
        with patch.dict(os.environ, {"VCF_SALT_CONFIG": str(cfg_path)}, clear=False):
            from raas_mcp import config_file as uc
            uc.invalidate_cache()
            from raas_mcp import server_config
            cfg = server_config.load()
            assert cfg.approval_gate == ["cmd_*"]

    def test_missing_config_file_yields_defaults(self, tmp_path):
        nonexistent = str(tmp_path / "missing.yml")
        with patch.dict(os.environ, {"VCF_SALT_CONFIG": nonexistent}, clear=False):
            from raas_mcp import config_file as uc
            uc.invalidate_cache()
            from raas_mcp import server_config
            cfg = server_config.load()
            assert cfg.allowed_tools is None
            assert cfg.approval_gate == []


class TestToolIsAllowed:
    def test_none_allows_everything(self):
        from raas_mcp.server_config import tool_is_allowed
        assert tool_is_allowed("ret_get_minions", None) is True
        assert tool_is_allowed("cmd_route_cmd", None) is True

    def test_exact_name_match(self):
        from raas_mcp.server_config import tool_is_allowed
        assert tool_is_allowed("ret_get_minions", ["ret_get_minions"]) is True
        assert tool_is_allowed("cmd_route_cmd", ["ret_get_minions"]) is False

    def test_prefix_glob_match(self):
        from raas_mcp.server_config import tool_is_allowed
        assert tool_is_allowed("ret_get_minions", ["ret_*"]) is True
        assert tool_is_allowed("ret_get_jobs", ["ret_*"]) is True
        assert tool_is_allowed("cmd_route_cmd", ["ret_*"]) is False

    def test_empty_list_allows_nothing(self):
        from raas_mcp.server_config import tool_is_allowed
        assert tool_is_allowed("ret_get_minions", []) is False


class TestToolIsGated:
    def test_not_gated_when_gate_empty(self):
        from raas_mcp.server_config import tool_is_gated
        assert tool_is_gated("cmd_route_cmd", []) is False

    def test_exact_name_gated(self):
        from raas_mcp.server_config import tool_is_gated
        assert tool_is_gated("cmd_route_cmd", ["cmd_route_cmd"]) is True
        assert tool_is_gated("ret_get_minions", ["cmd_route_cmd"]) is False

    def test_prefix_glob_gated(self):
        from raas_mcp.server_config import tool_is_gated
        assert tool_is_gated("cmd_route_cmd", ["cmd_*"]) is True
        assert tool_is_gated("cmd_get_status", ["cmd_*"]) is True
        assert tool_is_gated("ret_get_minions", ["cmd_*"]) is False
