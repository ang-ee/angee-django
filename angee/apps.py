"""Native Django lifecycle for the prebuilt Angee host."""

from __future__ import annotations

import importlib

from django import conf
from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured


class AngeeConfig(AppConfig):
    """Import ordinary prebuilt concrete models before Django calls ready()."""

    default = True
    name = "angee"
    labels: tuple[str, ...]

    def import_models(self) -> None:
        super().import_models()
        assert self.apps is not None
        if tuple(self.apps.get_app_configs())[-1] is not self:
            raise ImproperlyConfigured("angee must be last in INSTALLED_APPS")
        module_name = getattr(conf.settings, "COMPOSE_RUNTIME_MODULE", "runtime")
        try:
            artifact = importlib.import_module(module_name)
        except ImportError as error:
            raise ImproperlyConfigured(
                f"Cannot import prebuilt runtime {module_name!r}; run uv sync or uv build first."
            ) from error
        identities = getattr(artifact, "RUNTIME_APP_IDENTITIES", None)
        current = tuple((config.name, config.label) for config in self.apps.get_app_configs())
        if identities != current:
            raise ImproperlyConfigured(
                "Installed Django apps differ from the prebuilt runtime; rebuild it explicitly."
            )
        labels = getattr(artifact, "RUNTIME_APPS", None)
        if not isinstance(labels, (list, tuple)) or not all(isinstance(label, str) for label in labels):
            raise ImproperlyConfigured("Prebuilt runtime has no app labels; rebuild it explicitly.")
        self.labels = tuple(labels)
        modules = getattr(artifact, "RUNTIME_MODEL_MODULES", None)
        integration_modules = getattr(artifact, "RUNTIME_INTEGRATION_MODULES", None)
        migrations = getattr(artifact, "RUNTIME_MIGRATION_MODULES", None)
        if not isinstance(modules, tuple) or not all(isinstance(name, str) for name in modules):
            raise ImproperlyConfigured("Prebuilt runtime has no model import list; rebuild it explicitly.")
        if not isinstance(integration_modules, tuple) or not all(isinstance(name, str) for name in integration_modules):
            raise ImproperlyConfigured("Prebuilt runtime has no integration import list; rebuild it explicitly.")
        if not isinstance(migrations, dict):
            raise ImproperlyConfigured("Prebuilt runtime has no migration mapping; rebuild it explicitly.")
        configured = dict(getattr(conf.settings, "MIGRATION_MODULES", {}))
        for label, path in migrations.items():
            if not isinstance(label, str) or path is not None and not isinstance(path, str):
                raise ImproperlyConfigured("Prebuilt runtime contains an invalid migration mapping.")
            if label in configured and configured[label] != path:
                raise ImproperlyConfigured(f"MIGRATION_MODULES[{label!r}] changed; rebuild the runtime explicitly.")
            configured[label] = path
        conf.settings.MIGRATION_MODULES = configured
        for name in (*modules, *integration_modules):
            if not name.startswith(f"{module_name}."):
                raise ImproperlyConfigured(f"Invalid prebuilt module {name!r}")
            importlib.import_module(name)
