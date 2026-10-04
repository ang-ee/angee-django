"""Settings composer for an already-loaded Django settings namespace."""

from __future__ import annotations

import sys
from collections.abc import Iterable, MutableMapping
from typing import Any

from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured

from angee.compose.appgraph import AppGraph
from angee.compose.autoconfig import AutoConfig
from angee.compose.history import HistoricalRuntimeConfig, historical_labels
from angee.paths import resolve_path


class Composer:
    """Compose Angee's Django settings from project-declared apps."""

    def __init__(self, namespace: MutableMapping[str, Any]) -> None:
        """Store the settings namespace being composed."""

        self.namespace = namespace

    def compose_settings(self, *, project_apps: object | None = None) -> None:
        """Apply Angee's composed settings into ``namespace``."""

        installed_apps = self.namespace.get("INSTALLED_APPS")
        if installed_apps is None:
            raise ImproperlyConfigured("settings must define INSTALLED_APPS")
        root_apps = self._app_entries(installed_apps)
        declared_apps = None if project_apps is None else self._app_entries(project_apps)

        runtime_setting = self.namespace.get("ANGEE_RUNTIME_DIR")
        if runtime_setting is None:
            raise ImproperlyConfigured("settings must define ANGEE_RUNTIME_DIR")
        runtime_dir = resolve_path(runtime_setting)

        app_configs = AppGraph().resolve(root_apps, declared_roots=declared_apps)
        installed_labels = {config.label for config in app_configs}
        history = [
            HistoricalRuntimeConfig(runtime_dir, self.namespace.get("ANGEE_RUNTIME_MODULE", "runtime"), label)
            for label in historical_labels(runtime_dir)
            if label not in installed_labels
        ]
        self.namespace["INSTALLED_APPS"] = list(app_configs)
        self._set_composer_setting("ROOT_URLCONF", "angee.urls")
        self._set_composer_setting("ASGI_APPLICATION", "angee.asgi.application")
        self._set_composer_setting("ANGEE_RUNTIME_DIR", runtime_dir)
        runtime_parent = str(runtime_dir.parent.resolve())
        if runtime_parent in sys.path:
            sys.path.remove(runtime_parent)
        sys.path.insert(0, runtime_parent)

        AutoConfig.apply_installed(self.namespace)
        self.namespace["INSTALLED_APPS"].extend(history)

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

    def _set_composer_setting(self, key: str, value: object) -> None:
        """Assign a Composer-owned setting, rejecting conflicting project values."""

        if key in self.namespace:
            current = resolve_path(self.namespace[key]) if key == "ANGEE_RUNTIME_DIR" else self.namespace[key]
            if current != value:
                raise ImproperlyConfigured(f"Project settings define Composer-owned setting {key}")
        self.namespace[key] = value
