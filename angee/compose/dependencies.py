"""Project dependency projection for the composed addon set."""

from __future__ import annotations

import tomllib
from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path

from django.apps import AppConfig
from hatch_angee import ManifestError, ProjectError, compile_dependencies, parse_manifest, write_block

IN_WHEEL_ADDONS = frozenset(
    {
        "angee.base",
        "angee.compose",
        "angee.graphql",
        "angee.jobs",
    }
)
# Their dependencies are already carried by the django-angee wheel.


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

    The Django app graph owns which addons are composed. Each folder addon's
    co-located ``addon.toml`` owns its dependencies, and hatch-angee owns parsing,
    union compilation, and the round-trip-safe pyproject edit. The four in-wheel
    core addons are excluded because their dependencies remain in the wheel's
    static metadata.
    """

    def __init__(self, addons: Iterable[AppConfig], *, project_dir: Path | None) -> None:
        """Store the resolved app graph and host project directory."""

        self.addons = tuple(addons)
        self.project_dir = project_dir

    @property
    def pyproject_path(self) -> Path | None:
        """Return the host pyproject, if a project directory was discovered."""

        return self.project_dir / "pyproject.toml" if self.project_dir is not None else None

    def compile(self) -> tuple[str, ...]:
        """Compile the enabled folder-addon dependency union without writing."""

        try:
            manifests = tuple(
                parse_manifest(marker)
                for addon in self.addons
                if addon.name not in IN_WHEEL_ADDONS
                and (marker := Path(addon.path) / "addon.toml").is_file()
            )
            return compile_dependencies(manifests)
        except (ManifestError, ProjectError, OSError) as error:
            raise RuntimeError(str(error)) from error

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
