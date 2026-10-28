"""Native Django app configurations for explicit build and prebuilt startup."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from django import conf
from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured

if TYPE_CHECKING:
    from angee.compose.runtime import Runtime


def _require_last(config: AppConfig) -> None:
    """Concrete models must be imported after all native source model hooks."""

    assert config.apps is not None  # Django binds the registry before phase 2.
    if tuple(config.apps.get_app_configs())[-1] is not config:
        raise ImproperlyConfigured("angee.compose must be last in INSTALLED_APPS")


class ComposeConfig(AppConfig):
    """Import ordinary prebuilt concrete models before Django calls ready()."""

    default = True
    name = "angee.compose"
    labels: tuple[str, ...]

    def import_models(self) -> None:
        super().import_models()
        _require_last(self)
        module_name = getattr(conf.settings, "COMPOSE_RUNTIME_MODULE", "runtime")
        try:
            artifact = importlib.import_module(module_name)
        except ImportError as error:
            raise ImproperlyConfigured(
                f"Cannot import prebuilt runtime {module_name!r}; run uv sync or uv build first."
            ) from error
        identities = getattr(artifact, "RUNTIME_APP_IDENTITIES", None)
        assert self.apps is not None  # Django binds the registry before phase 2.
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


class BuildComposeConfig(AppConfig):
    """Run the single compiler in an explicitly selected native build process."""

    default = False
    name = "angee.compose"
    runtime: Runtime

    def import_models(self) -> None:
        super().import_models()
        _require_last(self)
        # Source AppConfig hooks have completed; concrete classes must exist
        # before native ready callbacks can inspect authentication or resources.
        self.runtime = self.create_runtime()
        self.runtime.emit_sources()
        self.runtime.import_generated_models()

    def create_runtime(self) -> Runtime:
        """Let native host AppConfig specializations supply their build policy."""

        from angee.compose.runtime import Runtime

        return Runtime.from_django()
