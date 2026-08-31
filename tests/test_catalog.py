"""Unit tests for raas_mcp.catalog — tool generation from api_discovery.json."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_catalog(discovery: dict[str, Any]):
    """Rebuild catalog._CATALOG from a provided discovery dict and return it."""
    import raas_mcp.catalog as catalog_mod

    with patch("raas_mcp.catalog._load_raw_discovery", return_value=discovery):
        catalog_mod._CATALOG = None  # force reload
        return catalog_mod._get_catalog()


# ---------------------------------------------------------------------------
# T008 — catalog generation tests (no allowlist filtering; filtering in T019)
# ---------------------------------------------------------------------------

class TestToolNameDerivation:
    def test_name_is_resource_underscore_method(self, minimal_discovery):
        catalog = _build_catalog(minimal_discovery)
        assert "ret_get_minions" in catalog
        assert "cmd_route_cmd" in catalog

    def test_entry_records_resource_and_method(self, minimal_discovery):
        catalog = _build_catalog(minimal_discovery)
        entry = catalog["ret_get_minions"]
        assert entry.resource == "ret"
        assert entry.method == "get_minions"
        assert entry.tool_name == "ret_get_minions"


class TestInputSchemaExtraction:
    def test_schema_properties_extracted(self, minimal_discovery):
        import raas_mcp.catalog as catalog_mod

        with patch("raas_mcp.catalog._load_raw_discovery", return_value=minimal_discovery):
            catalog_mod._CATALOG = None
            tools = catalog_mod.build_tool_list(allowed=None)

        tool_map = {t.name: t for t in tools}
        cmd_tool = tool_map["cmd_route_cmd"]
        assert cmd_tool.inputSchema is not None
        props = cmd_tool.inputSchema.get("properties", {})
        assert "tgt" in props
        assert "fun" in props

    def test_schema_required_extracted(self, minimal_discovery):
        catalog = _build_catalog(minimal_discovery)
        entry = catalog["cmd_route_cmd"]
        assert "tgt" in entry.required_params
        assert "fun" in entry.required_params
        assert "arg" not in entry.required_params

    def test_known_params_extracted(self, minimal_discovery):
        catalog = _build_catalog(minimal_discovery)
        entry = catalog["cmd_route_cmd"]
        assert entry.known_params == frozenset({"tgt", "fun", "arg"})

    def test_empty_schema_yields_empty_known_params(self, minimal_discovery):
        catalog = _build_catalog(minimal_discovery)
        entry = catalog["ret_get_minions"]
        assert entry.known_params == frozenset()
        assert entry.required_params == frozenset()


class TestDescriptionFallback:
    def test_description_from_formatted(self, minimal_discovery):
        catalog = _build_catalog(minimal_discovery)
        entry = catalog["ret_get_minions"]
        assert entry.description  # non-empty
        assert "minion" in entry.description.lower()

    def test_description_falls_back_to_resource_method_when_empty(self):
        discovery = {
            "foo": {
                "bar": {
                    "formatted": "",
                    "detailed": {"doc": "", "signature": ""},
                }
            }
        }
        catalog = _build_catalog(discovery)
        entry = catalog["foo_bar"]
        # help_text.build_rpc_command_help always produces at least
        # "RaaS — resource.method" as the short_help; both "foo.bar" and
        # "RaaS — foo.bar" are acceptable non-empty descriptions.
        assert entry.description  # non-empty
        assert "foo" in entry.description and "bar" in entry.description

    def test_description_falls_back_when_loadedmod_boilerplate(self):
        generic = "the loadedmod class allows for the module loaded onto the sub"
        discovery = {
            "foo": {
                "baz": {
                    "formatted": generic,
                    "detailed": {"doc": generic, "signature": ""},
                }
            }
        }
        catalog = _build_catalog(discovery)
        entry = catalog["foo_baz"]
        assert entry.description == "foo.baz"


class TestCollisionDetection:
    def test_collision_raises_system_exit(self):
        """Two entries whose resource_method concatenation is identical must fail fast."""
        discovery_with_collision = {
            "ret_get": {
                "minions": {
                    "formatted": "Method A",
                    "detailed": {"doc": "A", "signature": ""},
                }
            },
            "ret": {
                "get_minions": {
                    "formatted": "Method B",
                    "detailed": {"doc": "B", "signature": ""},
                }
            },
        }
        with pytest.raises(SystemExit):
            _build_catalog(discovery_with_collision)


class TestBuildToolList:
    def test_build_tool_list_none_returns_all(self, minimal_discovery):
        import raas_mcp.catalog as catalog_mod

        with patch("raas_mcp.catalog._load_raw_discovery", return_value=minimal_discovery):
            catalog_mod._CATALOG = None
            tools = catalog_mod.build_tool_list(allowed=None)

        names = {t.name for t in tools}
        assert "ret_get_minions" in names
        assert "cmd_route_cmd" in names
        assert len(names) == 2

    def test_build_tool_list_returns_mcp_tool_objects(self, minimal_discovery):
        from mcp import types as mcp_types
        import raas_mcp.catalog as catalog_mod

        with patch("raas_mcp.catalog._load_raw_discovery", return_value=minimal_discovery):
            catalog_mod._CATALOG = None
            tools = catalog_mod.build_tool_list(allowed=None)

        for tool in tools:
            assert isinstance(tool, mcp_types.Tool)
            assert tool.name
            assert tool.description
            assert tool.inputSchema is not None

    # ---------------------------------------------------------------------------
    # T019 — allowlist filter tests (implementation added in T020)
    # ---------------------------------------------------------------------------

    def test_build_tool_list_prefix_glob_returns_matching_only(self, minimal_discovery):
        import raas_mcp.catalog as catalog_mod

        with patch("raas_mcp.catalog._load_raw_discovery", return_value=minimal_discovery):
            catalog_mod._CATALOG = None
            tools = catalog_mod.build_tool_list(allowed=["ret_*"])

        names = {t.name for t in tools}
        assert "ret_get_minions" in names
        assert "cmd_route_cmd" not in names

    def test_build_tool_list_exact_name_returns_one(self, minimal_discovery):
        import raas_mcp.catalog as catalog_mod

        with patch("raas_mcp.catalog._load_raw_discovery", return_value=minimal_discovery):
            catalog_mod._CATALOG = None
            tools = catalog_mod.build_tool_list(allowed=["ret_get_minions"])

        assert len(tools) == 1
        assert tools[0].name == "ret_get_minions"

    def test_build_tool_list_empty_allowed_returns_empty(self, minimal_discovery):
        import raas_mcp.catalog as catalog_mod

        with patch("raas_mcp.catalog._load_raw_discovery", return_value=minimal_discovery):
            catalog_mod._CATALOG = None
            tools = catalog_mod.build_tool_list(allowed=[])

        assert tools == []

    def test_missing_catalog_raises_system_exit(self):
        import raas_mcp.catalog as catalog_mod

        with patch("raas_mcp.catalog._load_raw_discovery", return_value=None):
            catalog_mod._CATALOG = None
            with pytest.raises(SystemExit):
                catalog_mod.build_tool_list(allowed=None)
