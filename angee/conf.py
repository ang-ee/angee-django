"""Project settings contract loading for the composed Django settings module."""

from __future__ import annotations

import importlib
import os
import runpy
import sys
from collections.abc import Iterable, Mapping, MutableMapping
from copy import deepcopy
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

import django_yamlconf
import environ
from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured

from angee.addons import addon_manifest
from angee.compose import yamlconf
from angee.compose.declarations import AppDeclarationSource, BuildIntegration
from angee.project import (
    PROJECT_SETTINGS_ENV,
    PROJECT_YAML_NAME,
    PROJECT_YAML_SETTINGS,
    find_project_dir,
    project_dir,
)
from angee.utils.paths import resolve_path

if TYPE_CHECKING:
    from angee.compose.runtime import Runtime


DEFAULTS_SETTINGS_MODULE = "angee.defaults"


def prepend_import_paths(paths: Iterable[Path]) -> None:
    """Put import paths first and refresh the already-loaded framework package.

    ``generated runtime settings`` imports the host package before project addon roots are
    known. Its namespace path must gain the configured ``angee`` portions after
    those roots enter ``sys.path``.
    """

    import_paths = tuple(path.resolve() for path in paths if path.exists())
    for import_path in reversed(import_paths):
        sys_path_entry = str(import_path)
        if sys_path_entry in sys.path:
            sys.path.remove(sys_path_entry)
        sys.path.insert(0, sys_path_entry)
    package = sys.modules.get("angee")
    package_path = getattr(package, "__path__", None)
    portions = tuple(str(path / "angee") for path in import_paths if (path / "angee").is_dir())
    if package is not None and package_path is not None and portions:
        package.__path__ = list(dict.fromkeys((*portions, *(str(path) for path in package_path))))


class ProjectContract:
    """Load a project's settings contract and ask Angee to compose Django settings."""

    def __init__(self, namespace: MutableMapping[str, Any]) -> None:
        """Store the settings module namespace being populated."""

        self.namespace = namespace
        self.env = environ.Env()
        self.project_apps: object = ()

    def compose(
        self, *, root: Path | None = None, runtime_dir: Path | None = None,
        compose_config: str = "angee.compose.apps.ComposeConfig", error_reporting: bool = True,
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

    def _start_error_reporting(self) -> None:
        """Start Sentry once per process when the deployment supplies ``SENTRY_DSN``.

        Every Django, ASGI, Celery and management-command process composes
        settings here, before ``django.setup()``, which is where Sentry's Django
        and Celery integrations must start. The SDK reads ``SENTRY_DSN``,
        ``SENTRY_ENVIRONMENT`` and ``SENTRY_RELEASE`` itself.

        Sentry enables its native integrations for installed libraries. Addons
        contribute native integration classes to ``SENTRY_DISABLED_INTEGRATIONS``
        when their own error policy replaces an integration's capture. Request
        bodies and frame locals stay in the process.
        """

        # Metadata preparation uses load() without importing a telemetry SDK.
        # Only a fully composed application process starts native integrations.
        import sentry_sdk

        if not self.env.str("SENTRY_DSN", default="") or sentry_sdk.is_initialized():
            return
        sentry_sdk.init(
            disabled_integrations=self.namespace.get("SENTRY_DISABLED_INTEGRATIONS", ()),
            send_default_pii=False,
            include_local_variables=False,
            max_request_body_size="never",
        )

    def load(
        self, *, root: Path | None = None, authored_root: Path | None = None,
        authored_module: str | None = None, authored_yaml: Path | None = None,
    ) -> Path:
        """Load project settings and defaults without composing the Django app graph.

        This is the standalone settings seam for pre-Django bootstraps that need
        project declarations such as ``INSTALLED_APPS`` and
        ``ANGEE_ADDON_DIRS`` before addon dependencies make the apps importable.
        """

        self._reset_settings()
        if root is None:
            root = (find_project_dir() or Path.cwd().resolve()) if authored_root is not None else project_dir()
        else:
            root = resolve_path(root)
        source_root = root if authored_root is None else resolve_path(authored_root)
        source_yaml = source_root / "settings.yaml" if authored_yaml is None else resolve_path(authored_yaml)
        if authored_yaml is not None and (not source_yaml.is_file() or not os.access(source_yaml, os.R_OK)):
            raise ImproperlyConfigured(
                f"Packaged authored settings are missing or unreadable: {source_yaml}; rebuild the artifact."
            )
        if source_root == root:
            # Editable startup reads the live authored file once. Loading its
            # compiled snapshot as well would duplicate native append/prepend.
            source_yaml = root / "settings.yaml"
        self._read_project_env(root)
        settings_module = (
            self.env.str(PROJECT_SETTINGS_ENV, default=PROJECT_YAML_NAME)
            if authored_root is None else authored_module
        )
        prepend_import_paths((source_root,))
        if settings_module is None:
            declarations = ModuleType(PROJECT_YAML_NAME)
            declarations.__file__ = str(source_yaml)
        else:
            declarations = self._load_project_settings(source_root, settings_module)

        project_settings = ModuleType(declarations.__name__)
        # Native yamlconf overlays traverse dictionaries in place. Keep the
        # cached Python declarations intact for subsequent configuration loads.
        project_settings.__dict__.update(
            {
                name: deepcopy(value) if yamlconf.is_setting_name(name) and isinstance(value, dict) else value
                for name, value in vars(declarations).items()
            }
        )
        predefined_settings = self._load_project_overlays(
            project_settings, root, source_root=source_root, source_yaml=source_yaml,
        )
        self._reject_unexpected_sources(project_settings, root, source_yaml=source_yaml)
        project_yaml_settings = self._project_yaml_settings(project_settings, root)
        self.project_apps = getattr(project_settings, "INSTALLED_APPS", ())
        self._apply_defaults(project_settings, root, predefined_settings=predefined_settings)
        self.namespace[PROJECT_YAML_SETTINGS] = project_yaml_settings
        return root

    def _read_project_env(self, root: Path) -> None:
        """Load the project-root ``.env`` into the environment, process env winning.

        django-environ's canonical `.env` seam: a gitignored project `.env` (the
        stack's secrets file plus derived entries like ``DATABASE_URL``) supplies
        env vars for host-run ``manage.py`` commands, so ``uv run manage.py …``
        from the project root talks to the stack's database with the stack's
        SECRET_KEY. ``read_env`` never overwrites the real process environment
        (``overwrite=False``), so stack-managed services — whose env the operator
        sets explicitly — are unaffected. A missing file is a silent no-op.
        """

        env_file = root / ".env"
        if env_file.is_file():
            self.env.read_env(env_file)

    def _load_project_overlays(
        self, project_settings: ModuleType, root: Path, *, source_root: Path, source_yaml: Path,
    ) -> frozenset[str]:
        """Apply project YAML, explicit configuration and environment in native order.

        The native loader searches ancestor settings files. This host bounds its
        public pipeline to the selected project and explicit YAMLCONF_CONFFILE.
        Bootstrap-only variable names come from that same native pipeline, so
        library additions remain available for expansion without becoming Django
        settings. BASE_DIR remains the host's project-root setting.
        """

        with yamlconf.fail_on_yamlconf_errors():
            attributes = django_yamlconf.bootstrap_attributes(str(root))
            django_yamlconf.add_attr_info(attributes, "SOURCE_DIR", str(source_root))
            predefined_settings: frozenset[str] = frozenset(attributes) - {"BASE_DIR"}
            loader, loader_kwargs = django_yamlconf.get_loader("yaml")
            if loader is None:
                return predefined_settings
            if os.access(source_yaml, os.R_OK):
                django_yamlconf.load_conffile(attributes, project_settings, loader, loader_kwargs, str(source_yaml))
            project_yaml = root / "settings.yaml"
            if project_yaml.resolve() != source_yaml and os.access(project_yaml, os.R_OK):
                django_yamlconf.load_conffile(attributes, project_settings, loader, loader_kwargs, str(project_yaml))
            if final_conf := os.environ.get("YAMLCONF_CONFFILE"):
                django_yamlconf.load_conffile(attributes, project_settings, loader, loader_kwargs, final_conf)
            django_yamlconf.load_envdefs(attributes, project_settings)
            django_yamlconf.expand_attribute_refs(attributes)
            django_yamlconf.inject_attr(attributes, project_settings)
        return predefined_settings

    def _project_yaml_settings(self, project_settings: ModuleType, root: Path) -> frozenset[str]:
        """Project native effective-source metadata into the host's editable settings."""

        project_yaml = (root / "settings.yaml").resolve()
        names: set[str] = set()
        for name in django_yamlconf.defined_attributes(project_settings):
            info = django_yamlconf.get_attr_info(name, project_settings)
            source = info.get("source") if info else None
            if not isinstance(source, str):
                continue
            try:
                source_path = resolve_path(source)
            except ImproperlyConfigured, OSError, ValueError:
                continue
            if source_path == project_yaml:
                names.add(name)
        return frozenset(names)

    def _reject_unexpected_sources(
        self, project_settings: ModuleType, root: Path, *, source_yaml: Path,
    ) -> None:
        """Reject any implicit ancestor or foreign project-settings source."""

        allowed_sources = {
            yamlconf.YAMLCONF_INTERNAL_SOURCE,
            yamlconf.YAMLCONF_ENVIRONMENT_SOURCE,
            # Native yamlconf records the namespace name as the initial
            # source, even for the empty namespace of a YAML-only artifact.
            project_settings.__name__,
            str(source_yaml),
        }
        project_yaml = (root / "settings.yaml").resolve()
        if project_yaml.exists():
            allowed_sources.add(str(project_yaml))
        if final_conf := os.environ.get("YAMLCONF_CONFFILE"):
            allowed_sources.add(str(resolve_path(final_conf)))

        for name in django_yamlconf.get_cached_attributes(project_settings):
            for source in yamlconf.setting_sources(project_settings, name):
                if source in allowed_sources:
                    continue
                try:
                    source_path = str(resolve_path(source))
                except ImproperlyConfigured, OSError, TypeError, ValueError:
                    source_path = source
                if source_path not in allowed_sources:
                    raise ImproperlyConfigured(f"Unexpected django-yamlconf source {source!r}")

    def _reset_settings(self) -> None:
        """Remove previously composed Django settings from a reloaded module."""

        for setting in list(self.namespace):
            if setting == yamlconf.YAMLCONF_ATTRIBUTES or yamlconf.is_setting_name(setting):
                self.namespace.pop(setting, None)

    def _load_project_settings(
        self,
        root: Path,
        settings_module: str,
    ) -> ModuleType:
        """Load or synthesize the project settings module inside ``root``."""

        project_settings: ModuleType | None = None
        if existing_settings := sys.modules.get(settings_module):
            existing_file = getattr(existing_settings, "__file__", None)
            existing_path = Path(str(existing_file)).resolve() if existing_file else None
            if existing_path is not None and root in existing_path.parents:
                project_settings = existing_settings
            else:
                sys.modules.pop(settings_module, None)

        settings_path = root.joinpath(*settings_module.split(".")).with_suffix(".py")
        if project_settings is None and settings_path.exists():
            resolved_settings_path = settings_path.resolve()
            if root not in resolved_settings_path.parents:
                raise ImproperlyConfigured(
                    f"Loaded settings module {resolved_settings_path} is outside configured project root {root}"
                )
            project_settings = importlib.import_module(settings_module)
        elif project_settings is None and (root / "settings.yaml").exists():
            project_settings = ModuleType(settings_module)
            # Native yamlconf needs a target namespace, not an importable
            # synthetic settings.py module in the process module registry.
            project_settings.__file__ = str(root / "settings.yaml")

        if project_settings is None:
            raise ImproperlyConfigured("generated runtime settings needs settings.py or settings.yaml beside manage.py")

        settings_file = getattr(project_settings, "__file__", None)
        if not settings_file:
            raise ImproperlyConfigured("Loaded settings module has no __file__; cannot verify project root")
        loaded_path = Path(str(settings_file)).resolve()
        if root not in loaded_path.parents:
            raise ImproperlyConfigured(
                f"Loaded settings module {loaded_path} is outside configured project root {root}"
            )
        return project_settings

    def _apply_defaults(
        self,
        project_settings: ModuleType,
        root: Path,
        *,
        predefined_settings: frozenset[str],
    ) -> None:
        """Evaluate Angee defaults with project settings as the seed."""

        seed = {
            name: value
            for name, value in vars(project_settings).items()
            if yamlconf.is_setting_name(name) and name not in predefined_settings
        }
        seed.setdefault("BASE_DIR", root)
        if "DATABASE_URL" in os.environ:
            database = self.env.db()
            if "postgresql" in database.get("ENGINE", ""):
                # Permission reads may embed many index lookups; fresh-table
                # estimates can make PostgreSQL JIT slower than the query.
                options = database.setdefault("OPTIONS", {})
                options.setdefault("options", "-c jit=off")
                pool_default = environ.Env.parse_value(seed.get("ANGEE_DB_POOL", False), bool)
                if self.env.bool("ANGEE_DB_POOL", default=pool_default):
                    # Mirror Django's "Pooling doesn't support persistent connections"
                    # check while loading settings, before the connection is opened.
                    if database.get("CONN_MAX_AGE"):
                        raise ImproperlyConfigured("ANGEE_DB_POOL cannot be used with DATABASE_URL CONN_MAX_AGE")
                    # One thread-sensitive sync GraphQL call uses one connection
                    # per worker; a second slot permits concurrent async/Channels
                    # database work. Measure bursts with a live subscription.
                    options["pool"] = {
                        "min_size": self.env.int(
                            "ANGEE_DB_POOL_MIN_SIZE", default=int(seed.get("ANGEE_DB_POOL_MIN_SIZE", 0))
                        ),
                        "max_size": self.env.int(
                            "ANGEE_DB_POOL_MAX_SIZE", default=int(seed.get("ANGEE_DB_POOL_MAX_SIZE", 2))
                        ),
                        "timeout": self.env.float(
                            "ANGEE_DB_POOL_TIMEOUT", default=float(seed.get("ANGEE_DB_POOL_TIMEOUT", 5))
                        ),
                    }
            seed.setdefault("DATABASES", {"default": database})
        if "CACHE_URL" in os.environ:
            seed.setdefault("CACHES", {"default": self.env.cache()})
        if "EMAIL_BACKEND" in os.environ:
            seed.setdefault("EMAIL_BACKEND", os.environ["EMAIL_BACKEND"])
        if "EMAIL_URL" in os.environ:
            for email_setting, email_value in self.env.email_url().items():
                seed.setdefault(email_setting, email_value)

        self.namespace.update(
            {
                name: value
                for name, value in runpy.run_module(
                    DEFAULTS_SETTINGS_MODULE,
                    init_globals=seed,
                    run_name=f"{DEFAULTS_SETTINGS_MODULE}.__effective__",
                ).items()
                if (name == yamlconf.YAMLCONF_ATTRIBUTES or yamlconf.is_setting_name(name))
                and name not in predefined_settings
            }
        )

        if hasattr(project_settings, yamlconf.YAMLCONF_ATTRIBUTES):
            self.namespace[yamlconf.YAMLCONF_ATTRIBUTES] = getattr(
                project_settings,
                yamlconf.YAMLCONF_ATTRIBUTES,
            )


class ManifestDeclarations(AppDeclarationSource):
    """Project the host's canonical addon manifests into the compiler contract."""

    def dependencies(self, app: AppConfig, *, refresh: bool = False) -> tuple[str, ...]:
        manifest = addon_manifest(app, refresh=refresh)
        return manifest.depends_on if manifest is not None else ()

    def migrations(self, app: AppConfig) -> tuple[Mapping[str, Any], ...]:
        manifest = addon_manifest(app)
        return manifest.migrations if manifest is not None else ()


def project_settings_source(*, authored_module: str | None, source_path: str = "..") -> str:
    """Render ordinary settings wiring with portable authored-source references."""

    return (
        '"""Generated Django settings wiring; deployment inputs remain late-bound."""\n'
        "from pathlib import Path\n\n"
        "from angee.conf import ProjectContract\n\n"
        "ProjectContract(globals()).compose(\n"
        "    runtime_dir=Path(__file__).resolve().parent,\n"
        f"    authored_root=(Path(__file__).resolve().parent / {source_path!r}).resolve(),\n"
        f"    authored_module={authored_module!r},\n"
        "    authored_yaml=Path(__file__).with_suffix('.yaml'),\n"
        ")\n"
    )


class ProjectSettings(BuildIntegration):
    """Emit raw authored settings and native wiring, never resolved deployment values."""

    def render_sources(self, runtime: Runtime) -> Mapping[Path, str]:
        from django.conf import settings

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
        return {
            Path("settings.py"): project_settings_source(
                authored_module=module, source_path=os.path.relpath(source_root, runtime.runtime_dir),
            ),
            Path("settings.yaml"): source_yaml.read_text(encoding="utf-8") if source_yaml.is_file() else "{}\n",
        }
