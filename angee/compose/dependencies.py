"""Project dependency projection for the composed addon set."""

from __future__ import annotations

import tomllib
from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path

from django.apps import AppConfig
from hatch_angee import AddonManifest, ManifestError, ProjectError, compile_dependencies, write_block

from angee.addons import addon_manifest


class AddonDependencyGroupResult(StrEnum):
    """Describe what dependency projection did for the host project."""

    WRITTEN = "written"
    UNCHANGED = "unchanged"
    SKIPPED_NO_PYPROJECT = "skipped-no-pyproject"
    SKIPPED_NO_PROJECT_DIR = "skipped-no-project-dir"

    @property
    def skipped(self) -> bool:
        """Return whether projection had no host target to inspect or write."""

        return self in {self.SKIPPED_NO_PYPROJECT, self.SKIPPED_NO_PROJECT_DIR}


class AddonDependencyGroup:
    """Project the composed folder addons into the host dependency group.

    A co-located ``addon.toml`` marks a folder addon and owns its dependencies;
    hatch-angee owns parsing, union compilation, discovery, and the
    round-trip-safe pyproject edit. The normal build adapts the resolved Django
    app graph through :meth:`from_app_configs`; the pre-Django bootstrap adapts
    manifest roots through :meth:`from_manifest_roots`. Both paths converge on
    the same manifest compile/write core. Plain Django apps, including the
    framework core, have no manifest and therefore contribute nothing.
    """

    def __init__(self, manifests: Iterable[AddonManifest], *, project_dir: Path | None) -> None:
        """Store the resolved addon manifests and host project directory."""

        self.manifests = tuple(manifests)
        self.project_dir = project_dir

    @classmethod
    def from_app_configs(
        cls,
        addons: Iterable[AppConfig],
        *,
        project_dir: Path | None,
    ) -> AddonDependencyGroup:
        """Adapt an already-resolved Django app graph into the projector."""

        manifests = tuple(manifest for addon in addons if (manifest := addon_manifest(addon)) is not None)
        return cls(manifests, project_dir=project_dir)

    @classmethod
    def from_manifest_roots(
        cls,
        roots: Iterable[str],
        available_manifests: Iterable[AddonManifest],
        *,
        project_dir: Path | None,
    ) -> AddonDependencyGroup:
        """Resolve root folder addons and their manifest-only dependency closure.

        Discovery order is source precedence, so the first manifest for a name
        wins just as the first importable addon root does during Django boot.
        Dependencies without manifests are core or ordinary Django apps and do
        not contribute to the generated dependency group.
        """

        manifests_by_name: dict[str, AddonManifest] = {}
        for manifest in available_manifests:
            manifests_by_name.setdefault(manifest.name, manifest)

        root_names = tuple(roots)
        seen_roots: set[str] = set()
        for root in root_names:
            if root in seen_roots:
                raise RuntimeError(f"Duplicate root app {root!r}")
            seen_roots.add(root)

        ordered: list[AddonManifest] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(name: str) -> None:
            manifest = manifests_by_name.get(name)
            if manifest is None or name in visited:
                return
            if name in visiting:
                raise RuntimeError(f"Cycle in app dependencies at {name}")
            seen_dependencies: set[str] = set()
            for dependency in manifest.depends_on:
                if dependency in seen_dependencies:
                    raise RuntimeError(f"{name} declares duplicate dependency {dependency!r}")
                seen_dependencies.add(dependency)
            visiting.add(name)
            for dependency in sorted(manifest.depends_on):
                visit(dependency)
            visiting.remove(name)
            visited.add(name)
            ordered.append(manifest)

        for root in root_names:
            visit(root)
        return cls(ordered, project_dir=project_dir)

    @property
    def pyproject_path(self) -> Path | None:
        """Return the host pyproject, if a project directory was discovered."""

        return self.project_dir / "pyproject.toml" if self.project_dir is not None else None

    def compile(self) -> tuple[str, ...]:
        """Compile the enabled folder-addon dependency union without writing."""

        return compile_dependencies(self.manifests)

    def write(self) -> AddonDependencyGroupResult:
        """Project dependencies and report whether the host changed or was skipped."""

        pyproject_path = self.pyproject_path
        if pyproject_path is None:
            return AddonDependencyGroupResult.SKIPPED_NO_PROJECT_DIR
        if not pyproject_path.is_file():
            return AddonDependencyGroupResult.SKIPPED_NO_PYPROJECT
        dependencies = self.compile()
        try:
            original = pyproject_path.read_bytes()
            write_block(pyproject_path, dependencies)
            rendered = pyproject_path.read_bytes()
        except (ManifestError, ProjectError, OSError) as error:
            raise RuntimeError(str(error)) from error
        if rendered == original:
            return AddonDependencyGroupResult.UNCHANGED
        return AddonDependencyGroupResult.WRITTEN

    def check(self) -> AddonDependencyGroupResult:
        """Raise when the host dependency group differs from the compiled union."""

        pyproject_path = self.pyproject_path
        if pyproject_path is None:
            return AddonDependencyGroupResult.SKIPPED_NO_PROJECT_DIR
        if not pyproject_path.is_file():
            return AddonDependencyGroupResult.SKIPPED_NO_PYPROJECT
        expected = self.compile()
        try:
            with pyproject_path.open("rb") as stream:
                document = tomllib.load(stream)
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            raise RuntimeError(str(error)) from error
        groups = document.get("dependency-groups")
        actual = groups.get("addons") if isinstance(groups, dict) else None
        if not isinstance(actual, list) or not all(isinstance(dependency, str) for dependency in actual):
            raise RuntimeError(f"host addon dependency group is stale: {pyproject_path}")
        if tuple(actual) != expected:
            raise RuntimeError(f"host addon dependency group is stale: {pyproject_path}")
        return AddonDependencyGroupResult.UNCHANGED
