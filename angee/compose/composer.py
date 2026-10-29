"""Settings composer for an already-loaded Django settings namespace."""

from __future__ import annotations

import sys
from collections.abc import Iterable, MutableMapping
from typing import Any

from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured

from angee.compose.appgraph import AppGraph
from angee.compose.declarations import AppDeclarationSource
from angee.utils.autoconfig import AutoConfig
from angee.utils.paths import resolve_path


class Composer:
    """Compose native Django settings from project-declared apps."""

    def __init__(self, namespace: MutableMapping[str, Any]) -> None:
        """Store the settings namespace being composed."""

        self.namespace = namespace

    def compose_settings(
        self, *, project_apps: object | None = None, compose_config: str | None = None,
    ) -> None:
        """Apply composed settings into ``namespace``."""

        installed_apps = self.namespace.get("INSTALLED_APPS")
        if installed_apps is None:
            raise ImproperlyConfigured("settings must define INSTALLED_APPS")
        root_apps = self._app_entries(installed_apps)
        selected = AppConfig.create(compose_config) if compose_config is not None else None
        if selected is not None:
            root_apps = tuple(
                selected
                if (entry if isinstance(entry, AppConfig) else AppConfig.create(entry)).name == selected.name
                else entry
                for entry in root_apps
            )
            if selected not in root_apps:
                root_apps = (*root_apps, selected)
        declared_apps = None if project_apps is None else self._app_entries(project_apps)

        runtime_setting = self.namespace.get("COMPOSE_RUNTIME_DIR")
        if runtime_setting is None:
            raise ImproperlyConfigured("settings must define COMPOSE_RUNTIME_DIR")
        runtime_dir = resolve_path(runtime_setting)

        app_configs = AppGraph(AppDeclarationSource.configured(self.namespace)).resolve(
            root_apps, declared_roots=declared_apps,
        )
        # The compiler/importer is the final native phase-2 hook. Preserve
        # native identities and all dependency ordering among source apps.
        last = selected.name if selected is not None else "angee.compose"
        installed = [config for config in app_configs if config.name != last]
        installed.extend(config for config in app_configs if config.name == last)
        self.namespace["INSTALLED_APPS"] = installed
        self.namespace["COMPOSE_RUNTIME_DIR"] = runtime_dir
        runtime_parent = str(runtime_dir.parent.resolve())
        if runtime_parent in sys.path:
            sys.path.remove(runtime_parent)
        sys.path.insert(0, runtime_parent)

        AutoConfig.apply_installed(self.namespace)

    @staticmethod
    def _app_entries(value: object) -> tuple[str | AppConfig, ...]:
        """Validate and freeze one Django app declaration collection."""

        if isinstance(value, str | AppConfig):
            return (value,)
        if not isinstance(value, Iterable):
            raise ImproperlyConfigured("INSTALLED_APPS must be a string or iterable of app entries")
        entries = tuple(value)
        if not all(isinstance(entry, str | AppConfig) for entry in entries):
            raise ImproperlyConfigured("INSTALLED_APPS must contain app paths or AppConfig instances")
        return entries
