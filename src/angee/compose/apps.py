"""Django app config for the Angee composer — the build-time emit hook.

The composer is a plain ``AppConfig`` (a command host, not an addon) that turns
the installed addons' abstract source models into concrete Django apps under
``runtime/<label>/``. It hooks Django's app-populate **phase 2**: this app's
``import_models`` emits the runtime, and each source addon then *adopts* the
emitted concrete models for its own label (see
``angee.base.apps.BaseAddonConfig.import_models``). The full emit-then-adopt
lifecycle, and why the composer emits at all, are in ``docs/composer.md``.
"""

from __future__ import annotations

from django.apps import AppConfig


class ComposeConfig(AppConfig):
    """Composer app: emits the concrete runtime and hosts build commands.

    Listed before the base addon and source addons in ``INSTALLED_APPS`` so its
    ``import_models`` emits ``runtime/<label>/`` in app-populate phase 2 before
    any addon adopts it in the same phase. This single emit-then-adopt pass is
    why there is no build/run app-set split: the runtime is always freshly
    rendered from the abstract sources before it is loaded. See
    ``docs/composer.md``; ``AngeeRuntime`` is the emitter.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "angee.compose"

    def import_models(self) -> None:
        """Emit the concrete runtime in phase 2, before any addon adopts it.

        Runs first in the phase-2 ``import_models`` loop because
        ``COMPOSE_APP`` is ordered ahead of the base and source addons (see
        ``compose_defaults`` / ``_installed_apps``). It calls
        ``super().import_models()`` (the composer owns no source models), then
        ``emit_if_stale()`` writes ``runtime/<label>/models.py`` for every
        discovered addon to disk. Each source addon, later in the same loop,
        imports its emitted ``runtime.<label>.models`` to register the concrete
        models under its own label — the adoption step in
        ``BaseAddonConfig.import_models``. Emission here is write-only and
        idempotent; the destructive prune of orphaned labels belongs to the
        explicit ``angee build`` (``emit``).
        """

        super().import_models()
        # Deferred (phase-1 AppConfig rule): importing AngeeRuntime at module top
        # would transitively import model classes (angee.resources.models.Resource,
        # AngeeModel) during phase-1 AppConfig load, before the registry is ready.
        # By phase 2 the registry is populated, so this import — and the abstract
        # source models it introspects — is safe. ``from_settings`` owns the
        # ANGEE_RUNTIME_DIR contract and raises if it is missing.
        from angee.compose.runtime import AngeeRuntime

        AngeeRuntime.from_settings().emit_if_stale()
