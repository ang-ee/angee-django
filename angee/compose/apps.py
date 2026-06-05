"""Django app config for the Angee composer."""

from __future__ import annotations

from django.apps import AppConfig


class ComposeConfig(AppConfig):
    """Emit and import composed runtime models during app population."""

    default = True
    name = "angee.compose"
    depends_on = ("django_yamlconf",)
    emits_runtime_models = False

    def import_models(self) -> None:
        """Emit stale runtime files and import every emitted model module."""

        super().import_models()
        # Deferred (phase-1 AppConfig rule): importing Runtime at module top
        # would transitively import model classes (angee.resources.models.Resource,
        # AngeeModel) during phase-1 AppConfig load, before the registry is ready.
        # By phase 2 the registry is populated, so this import — and the abstract
        # source models it introspects — is safe. ``from_django`` owns the
        # ANGEE_RUNTIME_DIR contract and raises if it is missing.
        from angee.compose.runtime import Runtime

        Runtime.from_django().materialize_models()
