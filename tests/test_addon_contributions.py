"""Capability owners resolve authoritative declarations and real Python defaults."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured

from angee.base.addons import addon_contribution, addon_facts
from angee.graphql.schema import schema_parts_for
from angee.mcp.server import tool_registrar
from angee.web.build import WebRuntime
from tests.conftest import make_addon, write_addon_package


def test_conventional_schema_and_mcp_support_reexports_and_conditional_exports(tmp_path: Path) -> None:
    config = make_addon(path=tmp_path)
    (tmp_path / "implementation.py").write_text(
        'schemas = {"public": {}}\ndef register(server):\n    return None\n', encoding="utf-8"
    )
    (tmp_path / "schema.py").write_text("if True:\n    from .implementation import schemas\n", encoding="utf-8")
    (tmp_path / "mcp_tools.py").write_text("from .implementation import register\n", encoding="utf-8")
    assert "public" in schema_parts_for(config)
    assert tool_registrar(config) is importlib.import_module(f"{config.name}.implementation").register
    assert "graphql" not in addon_facts(config.name, "angee")
    assert addon_facts(config.name, "angee").get("mcp", {}) == {}


def test_explicit_graphql_declaration_wins_over_convention(tmp_path: Path) -> None:
    config = make_addon(path=tmp_path)
    (tmp_path / "selected.py").write_text(
        'schemas = {"chosen": {}}\ndef register(server):\n    return None\n', encoding="utf-8"
    )
    (tmp_path / "schema.py").write_text('raise AssertionError("unselected convention")\n', encoding="utf-8")
    write_addon_package(
        config,
        graphql={"schema": "selected.schemas"},
    )
    assert tuple(schema_parts_for(config)) == ("chosen",)


def test_missing_explicit_graphql_contribution_does_not_fall_back(tmp_path: Path) -> None:
    config = make_addon(path=tmp_path)
    (tmp_path / "schema.py").write_text('schemas = {"fallback": {}}\n', encoding="utf-8")
    write_addon_package(config, graphql={"schema": "missing.schemas"})
    with pytest.raises(ImproperlyConfigured, match="references"):
        schema_parts_for(config)


@pytest.mark.parametrize("module, owner", [("schema", schema_parts_for), ("mcp_tools", tool_registrar)])
def test_broken_present_module_preserves_internal_import_error(tmp_path: Path, module: str, owner) -> None:
    config = make_addon(path=tmp_path)
    (tmp_path / f"{module}.py").write_text("import angee_missing_test_dependency\n", encoding="utf-8")
    with pytest.raises(ImproperlyConfigured, match="failed to import") as failure:
        owner(config)
    assert isinstance(failure.value.__cause__, ModuleNotFoundError)
    assert failure.value.__cause__.name == "angee_missing_test_dependency"


@pytest.mark.parametrize("value", ["None", "'bad'", "b'bad'", "{'bad'}", "{'bad': 1}"])
def test_url_contributions_reject_nonsequences(tmp_path: Path, value: str) -> None:
    config = make_addon(path=tmp_path)
    (tmp_path / "urls.py").write_text(f"urlpatterns = {value}\n", encoding="utf-8")
    with pytest.raises(ImproperlyConfigured, match="must be iterable"):
        addon_contribution(config, "urls", "urlpatterns")


def test_web_codegen_reads_native_package_metadata(tmp_path: Path) -> None:
    config = make_addon(path=tmp_path, web={
        "package": "@example/operator",
        "codegen": {"schema": "operator", "sdl": "schema.graphql", "documents": "documents.ts"},
    })
    projection = WebRuntime((config,), dependencies={config.name: ()})
    assert projection.manifest["codegen"][0]["package"] == "@example/operator"
    assert projection.manifest["codegen"][0]["types"] is False
    assert "web" not in addon_facts(config.name, "angee")


def test_broken_native_web_package_fails_before_projection(tmp_path: Path) -> None:
    config = make_addon(path=tmp_path, web={"package": "@example/selected"})
    (tmp_path / "web/package.json").write_text("broken json", encoding="utf-8")
    with pytest.raises(ImproperlyConfigured, match="cannot read"):
        WebRuntime((config,), dependencies={config.name: ()})


@pytest.mark.parametrize("key, value", [("types", "false"), ("types", 1), ("sdl", 7), ("documents", False)])
def test_web_codegen_rejects_coercible_but_invalid_types(tmp_path: Path, key: str, value: object) -> None:
    raw = {"schema": "operator", "sdl": "schema.graphql", "documents": "documents.ts", key: value}
    config = make_addon(path=tmp_path, web={"package": "@example/operator", "codegen": raw})
    with pytest.raises(ImproperlyConfigured, match=f"codegen.{key} must be"):
        WebRuntime((config,), dependencies={config.name: ()})


@pytest.mark.parametrize(("capability", "explicit"), [("schema", False), ("schema", True), ("mcp", False)])
def test_present_null_export_is_invalid(tmp_path: Path, capability: str, explicit: bool) -> None:
    config = make_addon(path=tmp_path)
    module, attr = ("schema", "schemas") if capability == "schema" else ("mcp_tools", "register")
    (tmp_path / f"{module}.py").write_text(f"{attr} = None\n", encoding="utf-8")
    if explicit and capability == "schema":
        write_addon_package(config, graphql={"schema": "schema.schemas"})
    with pytest.raises(ImproperlyConfigured, match="must"):
        (schema_parts_for if capability == "schema" else tool_registrar)(config)
