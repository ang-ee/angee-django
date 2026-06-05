"""Django app config for the Angee composer."""

from __future__ import annotations

from django.apps import AppConfig


class ComposeConfig(AppConfig):
    """Bootstrap composed runtime models during app population."""

    default = True
    angee_addon = True
    name = "angee.compose"
    depends_on = ("django_yamlconf",)

    def import_models(self) -> None:
        """Emit the runtime if stale, then import the generated models.

        Runs in app-populate phase 2. ``emit_if_stale`` is write-only and
        idempotent, so a fresh or drifted runtime is healed file by file before
        it is imported — the runtime is always freshly rendered from the
        abstract sources before it is loaded. There is no build/run app-set
        split, and a missing runtime never surfaces as a cryptic
        ``AUTH_USER_MODEL`` resolution error: the swappable models exist by the
        time Django resolves them.
        """

        super().import_models()
        # Deferred (phase-1 AppConfig rule): importing Runtime at module top
        # would transitively import model classes (angee.resources.models.Resource,
        # AngeeModel) during phase-1 AppConfig load, before the registry is ready.
        # By phase 2 the registry is populated, so this import — and the abstract
        # source models it introspects — is safe. ``from_django`` owns the
        # ANGEE_RUNTIME_DIR contract and raises if it is missing.
        from angee.compose.runtime import Runtime

        runtime = Runtime.from_django()
        runtime.emit_if_stale()
        runtime.import_generated_models()
