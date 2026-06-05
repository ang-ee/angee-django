"""Django app config for the Angee composer."""

from __future__ import annotations

from django.apps import AppConfig
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class ComposeConfig(AppConfig):
    """Bootstrap composed runtime models during app population."""

    default = True
    angee_addon = True
    name = "angee.compose"
    depends_on = ("django_yamlconf",)

    def import_models(self) -> None:
        """Cheap-check runtime files, then import generated models when present."""

        super().import_models()
        # Deferred (phase-1 AppConfig rule): importing Runtime at module top
        # would transitively import model classes (angee.resources.models.Resource,
        # AngeeModel) during phase-1 AppConfig load, before the registry is ready.
        # By phase 2 the registry is populated, so this import — and the abstract
        # source models it introspects — is safe. ``from_django`` owns the
        # ANGEE_RUNTIME_DIR contract and raises if it is missing.
        from angee.compose.runtime import Runtime

        runtime = Runtime.from_django()
        try:
            should_import = runtime.bootstrap_check(
                strict=bool(getattr(settings, "ANGEE_RUNTIME_STRICT_BOOT", False)),
            )
        except RuntimeError as error:
            raise ImproperlyConfigured(
                f"{error}; run `angee build` to refresh generated runtime sources"
            ) from error
        if should_import:
            runtime.import_generated_models()
