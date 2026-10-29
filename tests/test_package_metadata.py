"""Installed metadata activates addons without importing disabled packages."""

from __future__ import annotations

import json
import sys
from importlib import metadata
from types import ModuleType

import pytest
from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured

from angee import addons
from angee.build import PackageDeclarations, project_settings_source


def distribution(tmp_path, monkeypatch, name, *, requirements=(), app=None, value=None, editable=False):
    app = app or name.replace("-", ".")
    value = value or app
    root = tmp_path / name
    package = root.joinpath(*app.split("."))
    package.mkdir(parents=True)
    document = (
        f'[project]\nname = {json.dumps(name)}\nversion = "3.0.0"\n'
        f'description = "Package data description"\ndependencies = {json.dumps(list(requirements))}\n'
        f'[project.entry-points."angee.addons"]\n{json.dumps(app)} = {json.dumps(value)}\n'
        '[tool.angee]\ncategory = "Data"\n[tool.angee.graphql]\nschema = "schema.custom"\n'
    )
    (package / "pyproject.toml").write_text(document)
    (package / "__init__.py").write_text("raise AssertionError('disabled addon imported')\n")
    info = root / f"{name.replace('-', '_')}-3.0.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.4\nName: {name}\nVersion: 3.0.0\nSummary: Installed description\nKeywords: one,two\n"
        + "".join(f"Requires-Dist: {requirement}\n" for requirement in requirements)
    )
    (info / "entry_points.txt").write_text(f"[angee.addons]\n{app} = {value}\n")
    if editable:
        (root / "pyproject.toml").write_text(document)
        (info / "direct_url.json").write_text(json.dumps({"url": root.as_uri(), "dir_info": {"editable": True}}))
    else:
        (info / "RECORD").write_text(f"{package.relative_to(root)}/pyproject.toml,,\n")
    dist = metadata.PathDistribution(info)
    entries = tuple(addons.addon_entry_points().values()) + tuple(dist.entry_points)
    monkeypatch.setattr(metadata, "entry_points", lambda **kwargs: entries)
    return dist, package


@pytest.fixture(autouse=True)
def empty_distribution_index(monkeypatch):
    monkeypatch.setattr(metadata, "entry_points", lambda **kwargs: ())


@pytest.mark.parametrize("editable", (False, True))
def test_disabled_catalogue_uses_distribution_paths_without_imports(tmp_path, monkeypatch, editable):
    dist, _ = distribution(tmp_path, monkeypatch, "probe-poison", editable=editable)
    facts = addons.facts_from_distribution(dist)
    assert facts == {
        "name": "probe.poison", "distribution": "probe-poison", "description": "Installed description",
        "keywords": ("one", "two"), "category": "Data", "depends_on": (),
    }
    assert "probe.poison" not in sys.modules


def test_requirement_markers_and_direct_references_own_activation(tmp_path, monkeypatch):
    distribution(tmp_path, monkeypatch, "probe-required")
    distribution(tmp_path, monkeypatch, "probe-optional")
    dist, _ = distribution(tmp_path, monkeypatch, "probe-root", requirements=(
        'probe-required @ https://example.invalid/required.whl ; extra != "excluded"',
        'probe-optional>=3 ; extra == "optional"',
        'plain-library>=1',
    ))
    assert addons.facts_from_distribution(dist)["depends_on"] == ("probe.required",)
    module = ModuleType("probe.root")
    module.__file__ = str(tmp_path / "__init__.py")
    config = AppConfig("probe.root", module)
    config.requires_apps = ("django.contrib.contenttypes",)
    assert addons.addon_dependencies(config) == ("probe.required", "django.contrib.contenttypes")


def test_requirement_metadata_overrides_package_toml(tmp_path, monkeypatch):
    distribution(tmp_path, monkeypatch, "probe-required")
    dist, package = distribution(tmp_path, monkeypatch, "probe-root", requirements=("probe-required>=3",))
    path = package / "pyproject.toml"
    path.write_text(path.read_text().replace('["probe-required>=3"]', '[]'))
    assert addons.facts_from_distribution(dist)["depends_on"] == ("probe.required",)


def test_entry_point_identity_collisions_fail_without_imports(tmp_path, monkeypatch):
    dist, _ = distribution(tmp_path, monkeypatch, "probe-root")
    monkeypatch.setattr(metadata, "entry_points", lambda **kwargs: (*dist.entry_points, *dist.entry_points))
    with pytest.raises(ImproperlyConfigured, match="Duplicate installed addon"):
        addons.addon_entry_points()


def test_vcs_projection_uses_explicit_distribution_identity_map():
    document = {
        "project": {
            "name": "probe-root", "entry-points": {"angee.addons": {"probe.root": "probe.root"}},
            "dependencies": ['external-package>=1', 'probe-required>=3; extra == ""'],
        },
    }
    assert addons.facts_from_toml(document, dependencies={"probe-required": "different.import_name"})["depends_on"] == (
        "different.import_name",
    )
    assert addons.facts_from_toml({"project": {"name": "ordinary"}}) == {}


def test_transitions_discover_sorted_paths_without_executing_sources(tmp_path, monkeypatch):
    _, package = distribution(tmp_path, monkeypatch, "probe-root")
    for label, name in (("zed", "last"), ("alpha", "first"), ("alpha", "__init__")):
        directory = package / "compose_migrations" / label
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{name}.py").write_text("raise AssertionError('transition imported')\n")
    module = ModuleType("probe.root")
    module.__file__ = str(package / "__init__.py")
    config = AppConfig("probe.root", module)
    assert PackageDeclarations().migrations(config) == (
        {"name": "first", "app_label": "alpha", "module": "compose_migrations.alpha.first"},
        {"name": "last", "app_label": "zed", "module": "compose_migrations.zed.last"},
    )


def test_prebuilt_settings_carry_only_plain_graph_facts():
    source = project_settings_source(
        authored_module=None, project_apps=("probe.root",),
        app_configs=(("probe.root", True, "probe.root", False),), autoconfig_modules=(),
    )
    assert "manifest" not in source
    assert "depends_on" not in source
    assert "app_configs=" in source
