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

import django_yamlconf
import environ
from django.core.exceptions import ImproperlyConfigured

from angee.compose import autoconfig as _autoconfig
from angee.compose.composer import Composer
from angee.paths import resolve_path
from angee.project import PROJECT_SETTINGS_ENV, PROJECT_YAML_NAME, project_dir

DEFAULTS_SETTINGS_MODULE = "angee.compose.defaults"
YAMLCONF_PREDEFINED_SETTINGS = frozenset(
    {
        "CPU_COUNT",
        "OS_MACHINE",
        "OS_NODE",
        "OS_PROCESSOR",
        "OS_RELEASE",
        "OS_SYSTEM",
        "PYTHON",
        "TOP_DIR",
        "USER",
        "VIRTUAL_ENV",
    }
)


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

    def compose(self) -> None:
        """Populate ``namespace`` from project settings, defaults, and addon contracts."""

        self._reset_settings()
        root = project_dir()
        settings_module = self.env.str(PROJECT_SETTINGS_ENV, default=PROJECT_YAML_NAME)

        prepend_import_paths((root,))
        project_settings = self._load_project_settings(root, settings_module)
        self._load_yaml_settings(project_settings, root)
        self._reject_unexpected_yamlconf_sources(project_settings, root, settings_module)
        self._apply_defaults(project_settings, root)

        prepend_import_paths((*self.namespace.get("ANGEE_ADDON_DIRS", ()), root))
        Composer(self.namespace).compose_settings()

    def _reset_settings(self) -> None:
        """Remove previously composed Django settings from a reloaded module."""

        for setting in list(self.namespace):
            if setting == _autoconfig.YAMLCONF_ATTRIBUTES or _autoconfig.is_setting_name(setting):
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

    def _load_yaml_settings(
        self,
        project_settings: ModuleType,
        root: Path,
    ) -> None:
        """Apply the project's YAML and environment settings overlay."""

        with _autoconfig.fail_on_yamlconf_errors():
            django_yamlconf.load(
                settings=project_settings,
                base_dir=str(root),
                project=PROJECT_YAML_NAME,
            )

    def _reject_unexpected_yamlconf_sources(
        self,
        project_settings: ModuleType,
        root: Path,
        settings_module: str,
    ) -> None:
        """Reject yamlconf's implicit ancestor ``settings.yaml`` cascade."""

        allowed_sources = {
            _autoconfig.YAMLCONF_INTERNAL_SOURCE,
            _autoconfig.YAMLCONF_ENVIRONMENT_SOURCE,
            settings_module,
        }
        project_yaml = (root / "settings.yaml").resolve()
        if project_yaml.exists():
            allowed_sources.add(str(project_yaml))
        if final_conf := os.environ.get("YAMLCONF_CONFFILE"):
            allowed_sources.add(str(resolve_path(final_conf)))

        for attribute in getattr(project_settings, _autoconfig.YAMLCONF_ATTRIBUTES, {}).values():
            sources = [attribute.get("source"), *(source for _value, source in attribute.get("history", ()))]
            for source in sources:
                if source in allowed_sources:
                    continue
                try:
                    source_path = str(resolve_path(str(source)))
                except (ImproperlyConfigured, OSError, TypeError, ValueError):
                    source_path = str(source)
                if source_path not in allowed_sources:
                    raise ImproperlyConfigured(f"Unexpected django-yamlconf source {source!r}")

    def _apply_defaults(
        self,
        project_settings: ModuleType,
        root: Path,
    ) -> None:
        """Evaluate Angee defaults with project settings as the seed."""

        seed = {
            name: value
            for name, value in vars(project_settings).items()
            if _autoconfig.is_setting_name(name) and name not in YAMLCONF_PREDEFINED_SETTINGS
        }
        seed.setdefault("BASE_DIR", root)

        if "DATABASE_URL" in os.environ:
            seed.setdefault("DATABASES", {"default": self.env.db()})
        if "CACHE_URL" in os.environ:
            seed.setdefault("CACHES", {"default": self.env.cache()})
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
                if (name == _autoconfig.YAMLCONF_ATTRIBUTES or _autoconfig.is_setting_name(name))
                and name not in YAMLCONF_PREDEFINED_SETTINGS
            }
        )

        if hasattr(project_settings, _autoconfig.YAMLCONF_ATTRIBUTES):
            self.namespace[_autoconfig.YAMLCONF_ATTRIBUTES] = getattr(
                project_settings,
                _autoconfig.YAMLCONF_ATTRIBUTES,
            )
