"""Project settings contract loading for the composed Django settings module."""

from __future__ import annotations

import importlib
import os
import runpy
import sys
from collections.abc import Iterable, MutableMapping
from pathlib import Path
from types import ModuleType
from typing import Any

import environ
import sentry_sdk
from django.core.exceptions import ImproperlyConfigured
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.logging import ignore_logger

from angee.compose import yamlconf
from angee.project import (
    PROJECT_SETTINGS_ENV,
    PROJECT_YAML_NAME,
    PROJECT_YAML_SETTINGS,
    project_dir,
)

DEFAULTS_SETTINGS_MODULE = "angee.compose.defaults"


def prepend_import_paths(paths: Iterable[Path]) -> None:
    """Put import paths at the front of ``sys.path`` preserving order."""

    for import_path in reversed(tuple(path.resolve() for path in paths if path.exists())):
        sys_path_entry = str(import_path)
        if sys_path_entry in sys.path:
            sys.path.remove(sys_path_entry)
        sys.path.insert(0, sys_path_entry)


class ProjectContract:
    """Load a project's settings contract and ask Angee to compose Django settings."""

    def __init__(self, namespace: MutableMapping[str, Any]) -> None:
        """Store the settings module namespace being populated."""

        self.namespace = namespace
        self.env = environ.Env()
        self._project_apps: object = ()

    def compose(self) -> None:
        """Publish project defaults and addon settings only after composition succeeds.

        Django may inspect the importing settings module during app discovery.
        Keeping work private prevents it from capturing a partial app graph, and
        a failed reload leaves the previously published settings intact. Error
        reporting starts as soon as the project environment is read, so a
        composition failure is reported too.
        """

        from angee.compose.composer import Composer

        published = self.namespace
        self.namespace = {}
        try:
            root = self.load()
            self._start_error_reporting()
            prepend_import_paths((*self.namespace.get("ANGEE_ADDON_DIRS", ()), root))
            Composer(self.namespace).compose_settings(project_apps=self._project_apps)
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

        Only the Django and Celery integrations are enabled, and request bodies
        and frame locals stay in the process. Unexpected GraphQL resolver errors
        reach Sentry only through the schema sanitizer's log record, which omits
        exception values. Strawberry's own error logger is ignored so each failure
        is reported once.
        """

        if not self.env.str("SENTRY_DSN", default="") or sentry_sdk.is_initialized():
            return
        ignore_logger("strawberry.execution")
        sentry_sdk.init(
            send_default_pii=False,
            include_local_variables=False,
            max_request_body_size="never",
            auto_enabling_integrations=False,
            integrations=[DjangoIntegration(), CeleryIntegration()],
        )

    def load(self) -> Path:
        """Load project settings and defaults without composing the Django app graph.

        This is the standalone settings seam for pre-Django bootstraps that need
        project declarations such as ``INSTALLED_APPS`` and
        ``ANGEE_ADDON_DIRS`` before addon dependencies make the apps importable.
        """

        self._reset_settings()
        root = project_dir()
        self._read_project_env(root)
        settings_module = self.env.str(PROJECT_SETTINGS_ENV, default=PROJECT_YAML_NAME)

        prepend_import_paths((root,))
        project_settings = self._load_project_settings(root, settings_module)
        yamlconf.load_project(project_settings, root)
        yamlconf.reject_unexpected_sources(project_settings, root, settings_module)
        project_yaml_settings = yamlconf.project_yaml_settings(project_settings, root)
        self._project_apps = getattr(project_settings, "INSTALLED_APPS", ())
        self._apply_defaults(project_settings, root)
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
            project_settings.__file__ = str(root / f"{PROJECT_YAML_NAME}.py")
            sys.modules[settings_module] = project_settings

        if project_settings is None:
            raise ImproperlyConfigured("angee.compose.settings needs settings.py or settings.yaml beside manage.py")

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
    ) -> None:
        """Evaluate Angee defaults with project settings as the seed."""

        seed = {
            name: value
            for name, value in vars(project_settings).items()
            if yamlconf.is_setting_name(name) and name not in yamlconf.YAMLCONF_PREDEFINED_SETTINGS
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
                and name not in yamlconf.YAMLCONF_PREDEFINED_SETTINGS
            }
        )

        if hasattr(project_settings, yamlconf.YAMLCONF_ATTRIBUTES):
            self.namespace[yamlconf.YAMLCONF_ATTRIBUTES] = getattr(
                project_settings,
                yamlconf.YAMLCONF_ATTRIBUTES,
            )
