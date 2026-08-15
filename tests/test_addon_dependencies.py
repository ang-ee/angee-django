"""Guard the addon dependency partition against the authoritative root tables."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT.parent
ADDON_ROOTS = tuple(
    root
    for root in (
        PROJECT_ROOT / "angee",
        SOURCE_ROOT / "angee-base" / "addons" / "angee",
        SOURCE_ROOT / "angee-messaging-bridges" / "addons" / "angee",
    )
    if root.is_dir()
)
CORE_DEPENDENCIES = {"django>=6.0", "pydantic>=2.13"}
MATRIX_MANIFEST = (
    SOURCE_ROOT
    / "angee-messaging-bridges"
    / "addons"
    / "angee"
    / "messaging_integrate_matrix"
    / "addon.toml"
)


def _read_toml(path: Path) -> dict[str, Any]:
    """Return the TOML document at ``path`` using the standard-library parser."""

    with path.open("rb") as stream:
        return tomllib.load(stream)


def _addon_manifests() -> tuple[Path, ...]:
    """Discover every first-party addon manifest under both Python source roots."""

    return tuple(
        manifest
        for root in ADDON_ROOTS
        for manifest in sorted(root.glob("*/addon.toml"))
    )


def test_addon_dependencies_partition_the_authoritative_root_tables() -> None:
    """Every addon dependency remains a verbatim member of its authoritative root table."""

    project = _read_toml(PROJECT_ROOT / "pyproject.toml")["project"]
    root_dependencies = set(project["dependencies"])
    optional_dependencies = project["optional-dependencies"]
    matrix_dependencies = set(optional_dependencies["matrix"])
    dependencies_by_manifest = {
        manifest: set(_read_toml(manifest)["addon"].get("dependencies", ()))
        for manifest in _addon_manifests()
    }
    declared_dependencies = set().union(*dependencies_by_manifest.values())

    assert dependencies_by_manifest, "no addon manifests were discovered"
    assert CORE_DEPENDENCIES <= root_dependencies
    assert declared_dependencies - matrix_dependencies == root_dependencies - CORE_DEPENDENCIES
    if MATRIX_MANIFEST.is_file():
        assert dependencies_by_manifest[MATRIX_MANIFEST] == matrix_dependencies

    root_declared_dependencies = root_dependencies | set().union(
        *(set(dependencies) for dependencies in optional_dependencies.values())
    )
    assert declared_dependencies <= root_declared_dependencies
