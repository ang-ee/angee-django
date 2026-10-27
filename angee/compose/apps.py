"""Django app config for the Angee composer."""

from __future__ import annotations

from django.apps import AppConfig


class ComposeConfig(AppConfig):
    """Bootstrap composed runtime models during app population."""

    default = True
    name = "angee.compose"

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
        # Source discovery and installed build integrations run in phase 2,
        # after AppConfig discovery and before ready(). No source model or
        # feature integration is imported during phase-1 config loading.
        from angee.compose.runtime import Runtime

        runtime = Runtime.from_django()
        runtime.configure_migration_modules()
        runtime.emit_if_stale()
        runtime.import_generated_models()
