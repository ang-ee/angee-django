"""Django app configuration for Angee's model foundation."""

from __future__ import annotations

from django.apps import AppConfig


class BaseConfig(AppConfig):
    """Wire model-layer registration after Django has populated apps."""

    default = True
    name = "angee.base"
    label = "base"
    depends_on = (
        "angee.compose",
        "django.contrib.contenttypes",
        "rebac",
        "reversion",
        "simple_history",
    )
    emits_runtime_models = False

    def ready(self) -> None:
        """Wire runtime model registration after Django populates apps."""

        super().ready()
        # Deferred: registration imports model-dependent modules that are unsafe
        # during phase 1.
        from angee.base.signals import register_revision_models

        register_revision_models()
