"""Settings composer for an already-loaded Django settings namespace."""

from __future__ import annotations

import sys
from collections.abc import Iterable, MutableMapping
from pathlib import Path
from typing import Any

from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured

from angee.compose.appgraph import AppGraph
from angee.compose.autoconfig import AutoConfig


class Composer:
    """Compose Angee's Django settings from project-declared apps."""

    def __init__(self, namespace: MutableMapping[str, Any]) -> None:
        """Store the settings namespace being composed."""

        self.namespace = namespace
        self.app_configs: tuple[AppConfig, ...] = ()

    def compose_settings(self) -> None:
        """Apply Angee's composed settings into ``namespace``."""

        installed_apps = self.namespace.get("INSTALLED_APPS")
        if installed_apps is None:
            raise ImproperlyConfigured("settings must define INSTALLED_APPS")
        root_apps: tuple[str | AppConfig, ...]
        if isinstance(installed_apps, str | AppConfig):
            root_apps = (installed_apps,)
        else:
            if not isinstance(installed_apps, Iterable):
                raise ImproperlyConfigured("INSTALLED_APPS must be a string or iterable of app entries")
            root_entries: list[str | AppConfig] = []
            for entry in installed_apps:
                if not isinstance(entry, str | AppConfig):
                    raise ImproperlyConfigured("INSTALLED_APPS must contain app paths or AppConfig instances")
                root_entries.append(entry)
            root_apps = tuple(root_entries)

        runtime_setting = self.namespace.get("ANGEE_RUNTIME_DIR")
        if runtime_setting is None:
            raise ImproperlyConfigured("settings must define ANGEE_RUNTIME_DIR")
        runtime_dir = self.path_value(runtime_setting)

        self.app_configs = AppGraph().resolve(root_apps)
        self.namespace["INSTALLED_APPS"] = list(self.app_configs)
        self.set_composer_setting("ROOT_URLCONF", "angee.urls")
        self.set_composer_setting("ASGI_APPLICATION", "angee.asgi.application")
        self.set_composer_setting("ANGEE_RUNTIME_DIR", runtime_dir)
        runtime_parent = str(runtime_dir.parent.resolve())
        if runtime_parent in sys.path:
            sys.path.remove(runtime_parent)
        sys.path.insert(0, runtime_parent)

        autoconfig = AutoConfig(self.namespace)
        for app_config in self.app_configs:
            autoconfig.update_app(app_config)

        runtime_module = str(self.namespace.get("ANGEE_RUNTIME_MODULE", "runtime"))
        runtime_model_labels = {
            config.label for config in self.app_configs if getattr(config, "emits_runtime_models", False)
        }
        self.namespace["MIGRATION_MODULES"] = {
            **dict(self.namespace.get("MIGRATION_MODULES", {})),
            **{label: f"{runtime_module}.{label}.migrations" for label in sorted(runtime_model_labels)},
        }

    def path_value(self, value: object) -> Path:
        """Return ``value`` as an absolute path."""

        if isinstance(value, Path):
            return value.expanduser().resolve()
        if isinstance(value, str):
            return Path(value).expanduser().resolve()
        raise ImproperlyConfigured(f"Expected path setting value, got {value!r}")

    def set_composer_setting(self, key: str, value: object) -> None:
        """Assign a Composer-owned setting, rejecting conflicting project values."""

        if key in self.namespace:
            current = self.path_value(self.namespace[key]) if key == "ANGEE_RUNTIME_DIR" else self.namespace[key]
            if current != value:
                raise ImproperlyConfigured(f"Project settings define Composer-owned setting {key}")
        self.namespace[key] = value
