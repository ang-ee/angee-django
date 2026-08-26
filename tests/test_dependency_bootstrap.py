"""Guard the manifest-only dependency bootstrap for fresh hosts."""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from django.apps import AppConfig, apps

from angee.compose.bootstrap import bootstrap_dependency_group
from angee.compose.dependencies import AddonDependencyGroup, AddonDependencyGroupResult
from angee.project import PROJECT_DIR_ENV, PROJECT_SETTINGS_ENV


def _write_addon(
    addon_root: Path,
    name: str,
    *,
    depends_on: tuple[str, ...] = (),
    dependencies: tuple[str, ...] = (),
) -> Path:
    """Write one fake folder addon and return its directory."""

    addon_dir = addon_root.joinpath(*name.split("."))
    addon_dir.mkdir(parents=True)
    rendered_depends_on = ", ".join(f'"{dependency}"' for dependency in depends_on)
    rendered_dependencies = ", ".join(f'"{dependency}"' for dependency in dependencies)
    (addon_dir / "addon.toml").write_text(
        f'[addon]\nname = "{name}"\ndepends_on = [{rendered_depends_on}]\ndependencies = [{rendered_dependencies}]\n',
        encoding="utf-8",
    )
    return addon_dir


def _app_config(path: Path, name: str) -> AppConfig:
    """Return an AppConfig rooted at one fake folder addon."""

    module = ModuleType(name)
    module.__file__ = str(path / "apps.py")
    module.__path__ = [str(path)]  # type: ignore[attr-defined]
    return AppConfig(name, module)


def _read_toml(path: Path) -> dict[str, Any]:
    """Read a TOML document with the standard-library parser."""

    with path.open("rb") as stream:
        return tomllib.load(stream)


def test_manifest_bootstrap_matches_resolved_app_config_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manifest roots include transitive addons and share the build projector."""

    manifest_host = tmp_path / "manifest-host"
    app_config_host = tmp_path / "app-config-host"
    manifest_host.mkdir()
    app_config_host.mkdir()
    initial_pyproject = '[project]\nname = "host"\nversion = "0.0.0"\n\n[dependency-groups]\naddons = []\n'
    for host in (manifest_host, app_config_host):
        (host / "pyproject.toml").write_text(initial_pyproject, encoding="utf-8")

    addon_root = manifest_host / "addons"
    leaf_dir = _write_addon(addon_root, "example.leaf", dependencies=("leaf-package>=1",))
    middle_dir = _write_addon(
        addon_root,
        "example.middle",
        depends_on=("example.leaf",),
        dependencies=("middle-package>=2",),
    )
    root_dir = _write_addon(
        addon_root,
        "example.root",
        depends_on=("example.middle", "django.contrib.auth"),
        dependencies=("root-package>=3",),
    )
    _write_addon(addon_root, "example.unused", dependencies=("unused-package>=4",))
    (manifest_host / "settings.yaml").write_text(
        "INSTALLED_APPS:\n"
        "  - example.root\n"
        "ANGEE_ADDON_DIRS:\n"
        '  - "{BASE_DIR}/addons"\n'
        'ANGEE_RUNTIME_DIR: "{BASE_DIR}/runtime"\n',
        encoding="utf-8",
    )

    settings_module = "manifest_bootstrap_test_settings"
    monkeypatch.setenv(PROJECT_DIR_ENV, str(manifest_host))
    monkeypatch.setenv(PROJECT_SETTINGS_ENV, settings_module)
    monkeypatch.delenv("YAMLCONF_CONFFILE", raising=False)
    registered_apps = tuple(apps.app_configs.items())
    try:
        assert bootstrap_dependency_group() is AddonDependencyGroupResult.WRITTEN
    finally:
        sys.modules.pop(settings_module, None)
        if str(manifest_host) in sys.path:
            sys.path.remove(str(manifest_host))
    assert tuple(apps.app_configs.items()) == registered_apps

    app_configs = (
        _app_config(leaf_dir, "example.leaf"),
        _app_config(middle_dir, "example.middle"),
        _app_config(root_dir, "example.root"),
    )
    assert (
        AddonDependencyGroup.from_app_configs(app_configs, project_dir=app_config_host).write()
        is AddonDependencyGroupResult.WRITTEN
    )

    manifest_pyproject = manifest_host / "pyproject.toml"
    app_config_pyproject = app_config_host / "pyproject.toml"
    assert manifest_pyproject.read_bytes() == app_config_pyproject.read_bytes()
    assert _read_toml(manifest_pyproject)["dependency-groups"]["addons"] == [
        "leaf-package>=1",
        "middle-package>=2",
        "root-package>=3",
    ]
