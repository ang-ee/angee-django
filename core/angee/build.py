"""Host adapters for explicit settings and native model compilation."""

from __future__ import annotations

import argparse
import importlib
import os
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import django_yamlconf
import environ
from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import module_has_submodule

from angee import addons
from angee.compose.apps import BuildComposeConfig
from angee.compose.declarations import AppDeclarationSource, BuildIntegration
from angee.conf import ProjectContract, prepend_import_paths
from angee.project import PROJECT_SETTINGS_ENV, PROJECT_YAML_NAME
from angee.utils.paths import resolve_path
from angee.utils.yamlconf import YAMLCONF_ATTRIBUTES

if TYPE_CHECKING:
    from angee.compose.runtime import Runtime


class ProjectBuildContract(ProjectContract):
    """Compose authored project declarations in an explicit build process."""

    def compose(
        self, *, root: Path | None = None, runtime_dir: Path | None = None,
        compose_config: str = "angee.apps.AngeeConfig", error_reporting: bool = True,
        authored_root: Path | None = None, authored_module: str | None = None, authored_yaml: Path | None = None,
    ) -> None:
        """Publish project defaults and addon settings only after composition succeeds.

        Django may inspect the importing settings module during app discovery.
        Keeping work private prevents it from capturing a partial app graph, and
        a failed reload leaves the previously published settings intact. Error
        reporting starts with the composed addon policy, including when a
        composition failure is about to propagate.
        """

        from angee.compose.composer import Composer

        published = self.namespace
        self.namespace = {}
        try:
            root = self.load(
                root=root, authored_root=authored_root, authored_module=authored_module, authored_yaml=authored_yaml,
            )
            if runtime_dir is not None:
                # A build target owns only its output location; project roots,
                # deployment values and native YAML precedence remain unchanged.
                self.namespace["COMPOSE_RUNTIME_DIR"] = resolve_path(runtime_dir)
            authored_root = root if authored_root is None else resolve_path(authored_root)
            import_paths = (*self.namespace.get("ANGEE_ADDON_DIRS", ()), root)
            if authored_root != root:
                import_paths = (authored_root, *import_paths)
            prepend_import_paths(import_paths)
            entries = addons.addon_entry_points()
            selected_apps = []
            for entry in self.namespace["INSTALLED_APPS"]:
                if isinstance(entry, str) and entry in entries:
                    config = AppConfig.create(entries[entry].value)
                    if config.name != entry:
                        raise ImproperlyConfigured(f"Addon entry point {entry!r} disagrees with AppConfig.name")
                    selected_apps.append(config)
                else:
                    selected_apps.append(entry)
            self.namespace["INSTALLED_APPS"] = selected_apps
            try:
                Composer(self.namespace).compose_settings(
                    project_apps=self.project_apps, compose_config=compose_config,
                )
            finally:
                if error_reporting:
                    self._start_error_reporting()
            composed = self.namespace
        finally:
            self.namespace = published
        self._reset_settings()
        self.namespace.update(composed)


class ProjectBuildConfig(BuildComposeConfig):
    """Keep native migration history beside each composed app's generated models."""

    default = False
    name = "angee"

    def create_runtime(self) -> Runtime:
        # Native AppConfig discovery inspects module globals before settings
        # composition completes; expose no lazy settings object during phase 1.
        from django.conf import settings

        runtime = super().create_runtime()
        modules = dict(getattr(settings, "MIGRATION_MODULES", {}))
        for label in runtime.labels:
            modules.setdefault(label, f"{runtime.runtime_module}.{label}.migrations")
        settings.MIGRATION_MODULES = modules
        return runtime


class PackageDeclarations(AppDeclarationSource):
    """Adapt installed requirements and native source paths to the current compiler."""

    def dependencies(self, app: AppConfig, *, refresh: bool = False) -> tuple[str, ...]:
        return addons.addon_dependencies(app, refresh=refresh)

    def migrations(self, app: AppConfig) -> tuple[Mapping[str, Any], ...]:
        """Discover sorted transitions without importing their executable sources."""

        if not addons.is_angee_addon(app):
            return ()
        directory = Path(app.path) / "compose_migrations"
        return tuple(
            {"name": source.stem, "app_label": label.name,
             "module": f"compose_migrations.{label.name}.{source.stem}"}
            for label in sorted(directory.iterdir()) if label.is_dir()
            for source in sorted(label.glob("*.py")) if source.name != "__init__.py"
        ) if directory.is_dir() else ()


def project_settings_source(
    *, authored_module: str | None, source_path: str = "..", project_apps: tuple[str, ...],
    app_configs: tuple[tuple[str, bool, str | None, bool], ...], autoconfig_modules: tuple[str, ...],
) -> str:
    """Render native settings wiring from structural declarations only."""

    return (
        '\"\"\"Generated Django settings; deployment inputs remain late-bound.\"\"\"\n'
        "from pathlib import Path\n\n"
        "from angee.conf import ProjectContract\n\n"
        "ProjectContract(globals()).load_prebuilt(\n"
        "    runtime_dir=Path(__file__).resolve().parent,\n"
        f"    authored_root=(Path(__file__).resolve().parent / {source_path!r}).resolve(),\n"
        f"    authored_module={authored_module!r},\n"
        "    authored_yaml=Path(__file__).with_suffix('.yaml'),\n"
        f"    project_apps={project_apps!r},\n"
        f"    app_configs={app_configs!r},\n"
        f"    autoconfig_modules={autoconfig_modules!r},\n"
        ")\n"
    )


class ProjectSettings(BuildIntegration):
    """Emit raw authored settings and native wiring, never resolved deployment values."""

    def render_sources(self, runtime: Runtime) -> Mapping[Path, str]:
        from django.conf import settings

        namespace = {YAMLCONF_ATTRIBUTES: django_yamlconf.get_cached_attributes(settings)}
        if hasattr(settings, "_ANGEE_PROJECT_APPS"):
            namespace["_ANGEE_PROJECT_APPS"] = getattr(settings, "_ANGEE_PROJECT_APPS")
        return self.project_sources(runtime.addons, runtime_dir=runtime.runtime_dir, namespace=namespace)

    @staticmethod
    def project_sources(
        apps: tuple[AppConfig, ...], *, runtime_dir: Path, namespace: Mapping[str, Any],
    ) -> Mapping[Path, str]:
        """Package authored inputs and the build's structural declarations."""

        from types import SimpleNamespace

        settings = SimpleNamespace(**namespace)
        source_info = django_yamlconf.get_attr_info("SOURCE_DIR", settings)
        if source_info is None:
            # Native Django hosts using only the generic composer have no
            # ProjectContract declarations to package.
            return {}
        source_root = Path(source_info["value"])
        module = environ.Env().str(PROJECT_SETTINGS_ENV, default=PROJECT_YAML_NAME)
        if not source_root.joinpath(*module.split(".")).with_suffix(".py").is_file():
            module = None
        source_yaml = source_root / "settings.yaml"
        app_configs = []
        autoconfig_modules = []
        for app in apps:
            if isinstance(app, BuildComposeConfig):
                path = "angee.apps.AngeeConfig"
            else:
                cls = type(app)
                path = app.name if cls is AppConfig else f"{cls.__module__}.{cls.__qualname__}"
            app_configs.append((
                path, bool(getattr(app, "compose_root", False)),
                getattr(app, "compose_root_declaration", None), bool(getattr(app, "compose_required", False)),
            ))
            if module_has_submodule(app.module, "autoconfig"):
                autoconfig_modules.append(f"{app.name}.autoconfig")
        roots = settings._ANGEE_PROJECT_APPS
        project_apps = (roots,) if isinstance(roots, str) else tuple(roots)
        return {
            Path("settings.py"): project_settings_source(
                authored_module=module, source_path=os.path.relpath(source_root, runtime_dir),
                project_apps=project_apps, app_configs=tuple(app_configs),
                autoconfig_modules=tuple(autoconfig_modules),
            ),
            Path("settings.yaml"): source_yaml.read_text(encoding="utf-8") if source_yaml.is_file() else "{}\n",
        }


def build_runtime(root: Path, output: Path, *, check_history: bool) -> None:
    """Compile once in a fresh interpreter; validate tracked native history offline.

    This private worker never installs dependencies, authors migrations, opens a
    database, or provisions a deployment. The dependency job installs requirements.
    """

    import django
    from django.apps import apps
    from django.conf import settings
    from django.db.migrations.autodetector import MigrationAutodetector
    from django.db.migrations.loader import MigrationLoader
    from django.db.migrations.state import ProjectState

    root, output = root.resolve(), output.resolve()
    namespace: dict[str, Any] = {}
    ProjectBuildContract(namespace).compose(
        root=root, runtime_dir=output, compose_config="angee.build.ProjectBuildConfig", error_reporting=False,
    )
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
                    "run manage.py makemigrations, review and commit history before runtime build."
                ) from error
            module_file = getattr(module, "__file__", None)
            if not module_file or not Path(module_file).resolve().is_relative_to(root):
                raise RuntimeError(
                    f"Native migration package {module_name!r} must be authored inside this host project"
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
                "review and commit history before runtime build."
            )


def main() -> None:
    """Build the host in a fresh process before generated settings exist."""

    parser = argparse.ArgumentParser(description="Build the prebuilt Angee runtime from authored project settings")
    parser.add_argument("--root", type=Path, required=True, help="Authored project directory")
    parser.add_argument("--output", type=Path, required=True, help="Generated runtime directory")
    parser.add_argument("--check-history", action="store_true", help="Validate native history offline")
    options = parser.parse_args()
    build_runtime(options.root, options.output, check_history=options.check_history)


if __name__ == "__main__":
    main()
