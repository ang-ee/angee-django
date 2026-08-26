"""Pre-Django addon dependency bootstrap for a fresh Angee host."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from hatch_angee import discover

from angee.compose.dependencies import AddonDependencyGroup, AddonDependencyGroupResult
from angee.compose.project import ProjectContract


def _strings(value: Any, *, setting: str) -> tuple[str, ...]:
    """Return one settings value as strings without importing Django apps."""

    if isinstance(value, str):
        return (value,)
    if not isinstance(value, Iterable):
        raise RuntimeError(f"{setting} must be a string or iterable of strings")
    items = tuple(value)
    if not all(isinstance(item, str) for item in items):
        raise RuntimeError(f"{setting} must contain strings")
    return items


def bootstrap_dependency_group() -> AddonDependencyGroupResult:
    """Project the root addons' manifest closure before Django app loading."""

    namespace: dict[str, Any] = {}
    root = ProjectContract(namespace).load()
    root_apps = _strings(namespace.get("INSTALLED_APPS", ()), setting="INSTALLED_APPS")
    addon_dirs = tuple(Path(path) for path in namespace.get("ANGEE_ADDON_DIRS", ()))
    manifests = (manifest for _addon_dir, manifest in discover(addon_dirs))
    return AddonDependencyGroup.from_manifest_roots(
        root_apps,
        manifests,
        project_dir=root,
    ).write()


def main() -> None:
    """Run the standalone addon dependency bootstrap."""

    result = bootstrap_dependency_group()
    print(f"Addon dependency bootstrap: {result.value}")


if __name__ == "__main__":
    main()
