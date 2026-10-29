"""Native Hatch metadata and build adapters for an explicitly opted-in host."""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from hatchling.builders.hooks.plugin.interface import BuildHookInterface
from hatchling.metadata.plugin.interface import MetadataHookInterface
from hatchling.plugin import hookimpl

if TYPE_CHECKING:
    from hatch_angee import AddonManifest


def _project_sources(
    root: Path,
) -> tuple[dict[str, Any], tuple[tuple[Path, AddonManifest], ...], tuple[str, ...]]:
    """Resolve one declared closure without importing addon code."""

    from hatch_angee import discover

    from angee.addons import resolve_manifest_roots
    from angee.conf import ProjectContract
    from angee.utils.paths import resolve_path

    namespace: dict[str, Any] = {}
    project = ProjectContract(namespace)
    project.load(root=root)
    # Metadata loading keeps native YAML values; the build adapter needs one
    # absolute filesystem output, independent of the frontend's current cwd.
    namespace["COMPOSE_RUNTIME_DIR"] = resolve_path(root / Path(namespace["COMPOSE_RUNTIME_DIR"]).expanduser())
    declared = project.project_apps
    if isinstance(declared, str):
        declared = (declared,)
    if not isinstance(declared, Iterable):
        raise TypeError("A build host must declare INSTALLED_APPS as import names")
    project_apps: list[str] = []
    for app in declared:
        if not isinstance(app, str):
            raise TypeError("A build host must declare INSTALLED_APPS as import names")
        project_apps.append(app)
    addon_dirs: tuple[Path, ...] = namespace["ANGEE_ADDON_DIRS"]
    for addon_dir in addon_dirs:
        if not addon_dir.is_dir():
            raise FileNotFoundError(f"Configured addon source directory does not exist: {addon_dir}")
    available = tuple(discover(addon_dirs))
    selected = resolve_manifest_roots(namespace["INSTALLED_APPS"], (manifest for _path, manifest in available))
    # Native closure resolution returns the selected objects, preserving first
    # source precedence. Do not independently decide precedence for packaging.
    paths = {id(manifest): path for path, manifest in available}
    return namespace, tuple((paths[id(manifest)], manifest) for manifest in selected), tuple(project_apps)


class RuntimeMetadataHook(MetadataHookInterface):
    """Project the selected addon closure into native package metadata."""

    PLUGIN_NAME = "angee-runtime"

    def update(self, metadata: dict[str, Any]) -> None:
        """Set dynamic requirements and addon anchors without importing app code."""

        seed = self.config.get("dependencies", [])
        if not isinstance(seed, list) or not all(isinstance(requirement, str) for requirement in seed):
            raise TypeError("angee-runtime metadata dependencies must be a list of requirement strings")

        # Metadata preparation precedes installation of addon requirements. The
        # existing declaration loader must not compose/populate Django here.
        from hatch_angee import compile_dependencies
        from hatch_angee.hooks import ENTRY_POINT_GROUP

        _namespace, sources, _project_apps = _project_sources(Path(self.root).resolve())
        manifests = tuple(manifest for _path, manifest in sources)
        requirements = sorted({*seed, *compile_dependencies(manifests)})
        _check_distribution_requirements(requirements, source=f"{self.root}: runtime requirements")
        metadata["dependencies"] = requirements
        metadata.setdefault("entry-points", {})[ENTRY_POINT_GROUP] = {
            manifest.name: manifest.name
            for manifest in sorted(manifests, key=lambda manifest: manifest.name)
        }


def _copy_source(source: Path, destination: Path, *, exclude: Path | None = None) -> None:
    """Remap native Hatch source selection into the canonical import layout."""

    if source.is_dir():
        from hatchling.builders.sdist import SdistBuilder
        from hatchling.utils.fs import locate_file

        source = source.resolve()
        project_file = locate_file(str(source), "pyproject.toml", boundary=".git")
        owner = Path(project_file).parent if project_file is not None else source
        selection = SdistBuilder(str(owner), config=None if project_file is not None else {})
        excludes = ["**/node_modules", "**/venv", "**/dist", "**/build", "**/.env", "**/.env.*"]
        if exclude is not None and exclude.is_relative_to(owner):
            excludes.append("/" + exclude.relative_to(owner).as_posix())
        existing_excludes = selection.target_config.get("exclude", selection.build_config.get("exclude", []))
        if not isinstance(existing_excludes, list):
            raise TypeError(f"{project_file}: native Hatch source exclusions must be a list")
        selection.target_config["exclude"] = [*existing_excludes, *excludes]
        selection.target_config["skip-excluded-dirs"] = True
        # Only native file selection is invoked, never SdistBuilder.build or
        # another hook. Select this explicit addon/package, retaining its owning
        # project's Hatch/VCS exclusions in their original path context. The
        # owner's distribution may deliberately omit addons; its only-include
        # list does not decide this different, explicitly selected artifact.
        for included in selection.recurse_explicit_files({str(source): source.relative_to(owner).as_posix()}):
            path = Path(included.path)
            if not path.resolve().is_relative_to(source):
                raise ValueError(f"Cannot bundle source symlink outside {source}: {path}")
            _copy_source(path.resolve(), destination / path.relative_to(source))
    else:
        if source.name == ".env" or source.name.startswith(".env."):
            return
        if not source.resolve().is_relative_to(source.parent.resolve()):
            raise ValueError(f"Cannot bundle source file symlink outside its authored directory: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and destination.read_bytes() != source.read_bytes():
            raise ValueError(f"Competing authored package files at {destination}: {source}")
        shutil.copy2(source, destination)


def _check_distribution_requirements(requirements: Iterable[str], *, source: str) -> None:
    """Leave resolution to native tooling; refuse checkout-local direct references."""

    from packaging.requirements import Requirement

    for value in requirements:
        requirement = Requirement(value)
        if requirement.url:
            scheme = urlsplit(requirement.url).scheme
            if not scheme or scheme == "file" or scheme.endswith("+file"):
                raise ValueError(f"{source}: nonportable local requirement: {value}")


def _stage_pyproject(source: Path, destination: Path) -> None:
    """Retain standards/build metadata without checkout-specific uv overrides."""

    import tomlkit

    document = tomlkit.parse(source.read_text(encoding="utf-8"))
    # uv sources/workspaces/cache/build overrides configure the author's local
    # frontend, not the published distribution. Its regular PEP 508 requirements
    # remain unchanged; uv resolves those normally when building this sdist.
    if "tool" in document:
        document["tool"].pop("uv", None)
    _check_distribution_requirements(
        document.get("build-system", {}).get("requires", []), source=f"{source}: build-system.requires",
    )
    destination.write_text(tomlkit.dumps(document), encoding="utf-8")


def _stage_project(
    root: Path, stage: Path, sources: tuple[tuple[Path, AddonManifest], ...],
    namespace: dict[str, Any], project_apps: tuple[str, ...],
) -> None:
    """Make the sdist's canonical import layout without serializing effective settings."""

    import yaml

    for app_dir, manifest in sources:
        if manifest.permissions is not None:
            permission = (app_dir / manifest.permissions).resolve()
            if not permission.is_relative_to(app_dir.resolve()):
                raise ValueError(
                    f"{app_dir / 'addon.toml'}: addon.permissions {manifest.permissions!r} "
                    "is outside the bundled addon; move the authored file into its addon."
                )
            if Path(manifest.permissions).is_absolute():
                raise ValueError(
                    f"{app_dir / 'addon.toml'}: addon.permissions must be relative for a portable host artifact"
                )
        destination = stage.joinpath(*manifest.name.split("."))
        _copy_source(app_dir, destination)
        if not (destination / "addon.toml").is_file():
            raise ValueError(
                f"{app_dir / 'addon.toml'}: native source exclusions removed the required addon declaration"
            )
        if manifest.permissions is not None and not (destination / manifest.permissions).is_file():
            raise ValueError(
                f"{app_dir / 'addon.toml'}: native source exclusions removed "
                f"addon.permissions {manifest.permissions!r}"
            )
        # Explicit parent package initializers are authored namespace behavior;
        # preserve them when the source follows its declared module layout.
        parts = manifest.name.split(".")
        if tuple(app_dir.parts[-len(parts):]) == tuple(parts):
            for depth in range(1, len(parts)):
                parent = app_dir.parents[depth - 1]
                initializer = parent / "__init__.py"
                if initializer.is_file():
                    _copy_source(initializer, destination.parents[depth - 1] / "__init__.py")

    # Host settings and migration packages are authored source. Never dump the
    # evaluated namespace, which contains deployment credentials and env values.
    addon_namespaces = {manifest.name.split(".")[0] for _path, manifest in sources}
    for path in sorted(root.iterdir()):
        if path.is_file() and path.suffix == ".py":
            _copy_source(path, stage / path.name)
        elif (
            path.is_dir() and (path / "__init__.py").is_file() and path.name not in addon_namespaces
            and path.resolve() != namespace["COMPOSE_RUNTIME_DIR"]
        ):
            _copy_source(path, stage / path.name, exclude=namespace["COMPOSE_RUNTIME_DIR"])
    # Native history is authored input beside generated models. Select only
    # its package directories, then reuse the existing native Hatch selection
    # helper for every file. Retain inactive app histories as well; never stage
    # stale generated models or runtime output.
    runtime_source = Path(namespace["COMPOSE_RUNTIME_DIR"])
    runtime_target = stage.joinpath(*namespace["COMPOSE_RUNTIME_MODULE"].split("."))
    for history in sorted(runtime_source.glob("*/migrations")):
        if history.is_dir():
            _copy_source(history, runtime_target / history.parent.name / "migrations")
    yaml_path = root / "settings.yaml"
    declarations = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) if yaml_path.exists() else {}
    if declarations is None:
        declarations = {}
    if not isinstance(declarations, dict):
        raise ValueError(f"{yaml_path}: host settings must be a mapping")
    declarations["ANGEE_ADDON_DIRS"] = ["{SOURCE_DIR}"]
    declarations["INSTALLED_APPS"] = list(project_apps)
    declarations["COMPOSE_RUNTIME_MODULE"] = namespace["COMPOSE_RUNTIME_MODULE"]
    declarations["COMPOSE_RUNTIME_DIR"] = "{BASE_DIR}/" + namespace["COMPOSE_RUNTIME_MODULE"].replace(".", "/")
    (stage / "settings.yaml").write_text(yaml.safe_dump(declarations, sort_keys=True), encoding="utf-8")
    _stage_pyproject(root / "pyproject.toml", stage / "pyproject.toml")
    if (stage / "angee.py").exists():
        raise ValueError("The host's angee.py would shadow its django-angee dependency")
    core = Path(__file__).resolve().parent
    for path in sorted((stage / "angee").rglob("*")):
        if path.is_file() and (core / path.relative_to(stage / "angee")).is_file():
            raise ValueError(f"Bundled addon source overlaps its django-angee dependency: {path.relative_to(stage)}")


def build_runtime(root: Path, output: Path, *, check_history: bool) -> None:
    """Compile once in a fresh interpreter; validate tracked native history offline.

    This private worker never installs dependencies, authors migrations, opens a
    database, or provisions a deployment. Hatch supplies runtime dependencies.
    """

    import django
    from django.apps import apps
    from django.conf import settings
    from django.db.migrations.autodetector import MigrationAutodetector
    from django.db.migrations.loader import MigrationLoader
    from django.db.migrations.state import ProjectState

    from angee.build import ProjectBuildConfig, ProjectBuildContract

    root, output = root.resolve(), output.resolve()
    namespace: dict[str, Any] = {}
    ProjectBuildContract(namespace).compose(
        root=root, runtime_dir=output, compose_config="angee.build.ProjectBuildConfig", error_reporting=False,
    )
    if check_history and any(not path.is_relative_to(root) for path in namespace["ANGEE_ADDON_DIRS"]):
        raise RuntimeError("The portable host snapshot must resolve addon sources inside its own root")
    settings.configure(**namespace)
    django.setup()
    config = apps.get_app_config("angee")
    if not isinstance(config, ProjectBuildConfig):
        raise RuntimeError("The host build must select ProjectBuildConfig")
    runtime = config.runtime
    if check_history:
        migrated_labels = {label for label in runtime.labels if settings.MIGRATION_MODULES[label] is not None}
        for label in runtime.labels:
            module_name = settings.MIGRATION_MODULES[label]
            if module_name is None:
                continue
            try:
                module = importlib.import_module(module_name)
            except ImportError as error:
                raise RuntimeError(
                    f"Missing tracked native migration package {module_name!r}; "
                    "run manage.py makemigrations, review and commit history before uv build."
                ) from error
            module_file = getattr(module, "__file__", None)
            if not module_file or not Path(module_file).resolve().is_relative_to(root):
                raise RuntimeError(
                    f"Native migration package {module_name!r} must be authored inside this host artifact"
                )
        runtime.runtime_migrations().check()
        loader = MigrationLoader(None, ignore_no_migrations=True)
        if conflicts := loader.detect_conflicts():
            raise RuntimeError(f"Conflicting tracked migration leaves: {conflicts}")
        changes = MigrationAutodetector(loader.project_state(), ProjectState.from_apps(apps)).changes(
            graph=loader.graph, trim_to_apps=migrated_labels, convert_apps=migrated_labels,
        )
        if changes:
            raise RuntimeError(
                "Tracked native migration history does not match composed models for "
                f"{', '.join(sorted(changes))}; run manage.py makemigrations, "
                "review and commit history before uv build."
            )


def _compile(root: Path, output: Path, *, check_history: bool) -> None:
    """Isolate native Django app population from the Hatch frontend process."""

    script = (
        "import sys\nfrom pathlib import Path\nfrom angee.hatch import build_runtime\n"
        "build_runtime(Path(sys.argv[1]), Path(sys.argv[2]), check_history=sys.argv[3] == '1')\n"
    )
    subprocess.run(
        [sys.executable, "-P", "-c", script, str(root), str(output), "1" if check_history else "0"],
        cwd=root, check=True,
    )


class RuntimeBuildHook(BuildHookInterface):
    """Package one project-specific prebuilt host through native Hatch targets."""

    PLUGIN_NAME = "angee-runtime"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        root = Path(self.root).resolve()
        namespace, sources, project_apps = _project_sources(root)
        self._temporary = tempfile.TemporaryDirectory(prefix="angee-host-build-")
        temporary = Path(self._temporary.name)
        if version == "editable":
            output = namespace["COMPOSE_RUNTIME_DIR"]
            _compile(root, output, check_history=False)
            self.build_config.target_config["dev-mode-dirs"] = list(dict.fromkeys(
                str(path) for path in (root, *namespace["ANGEE_ADDON_DIRS"], output.parent)
            ))
            return

        stage = temporary / "host"
        stage.mkdir()
        _stage_project(root, stage, sources, namespace, project_apps)
        output = stage.joinpath(*namespace["COMPOSE_RUNTIME_MODULE"].split("."))
        _compile(stage, output, check_history=True)
        # Native Hatch owns file selection and archives. The explicit snapshot
        # prevents a source-tree walk from including .env or unrelated slots.
        self.build_config.set_exclude_all()
        build_data["force_include"].pop(os.path.join(self.root, "pyproject.toml"), None)
        for path in sorted(stage.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            relative = path.relative_to(stage)
            if self.target_name == "wheel" and relative.as_posix() in {"pyproject.toml", "settings.yaml"}:
                continue
            build_data["force_include"][str(path)] = relative.as_posix()

    def finalize(self, version: str, build_data: dict[str, Any], artifact_path: str) -> None:
        self._temporary.cleanup()


@hookimpl
def hatch_register_build_hook() -> type[RuntimeBuildHook]:
    """Expose the opt-in prebuilt host build hook alongside metadata."""

    return RuntimeBuildHook


@hookimpl
def hatch_register_metadata_hook() -> type[RuntimeMetadataHook]:
    """Expose the host's opt-in metadata hook to native Hatch discovery."""

    return RuntimeMetadataHook
