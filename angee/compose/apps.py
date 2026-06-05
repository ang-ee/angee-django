"""Django app config for the Angee composer."""

from __future__ import annotations

import sys

from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured


class ComposeConfig(AppConfig):
    """Import composed runtime models during app population."""

    default = True
    name = "angee.compose"
    depends_on = ("django_yamlconf",)

    def import_models(self) -> None:
        """Check runtime files, then import generated models."""

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
            if sys.argv[1:3] in (["angee", "build"], ["angee", "clean"]) and not sys.argv[3:]:
                if not runtime.is_current():
                    runtime.emit()
            else:
                runtime.check()
        except RuntimeError as error:
            raise ImproperlyConfigured(
                f"{error}; run `angee build` to refresh generated runtime sources"
            ) from error
        runtime.import_generated_models()
