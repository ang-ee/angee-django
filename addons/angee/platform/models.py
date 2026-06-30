"""Source models for the platform addon.

The platform console reflects the runtime the composer already built. ``Addon`` is
that reflection made persistent: one row per composed/available addon, **converged
from the app registry after migrate** — the same reconcile Django runs for
``django_content_type`` / ``auth_permission`` (see ``signals.py``). It is therefore
authoritatively *derived*, never authored: ``settings.yaml`` (enabled) and
``uv.lock`` (available) remain the source of truth; this table is the queryable
mirror that backs the console.

Scope is deliberately **local**: which addons are *available* (installed bundles +
local ``addon.toml``) and which lifecycle ``state`` each is in — ``enabled``
(composed), ``disabled`` (available but not composed), or ``removed`` (gone from the
env, the row kept as history; the reconcile marks state, it never deletes). The
remote marketplace — addons known from VCS provenance but not materialised — is
**not** here; the ``platform_integrate_vcs`` addon extends ``Addon`` with that tier.

``PlatformExplorer`` stays a table-less REBAC type anchor for the schema explorer.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import models, router, transaction
from rebac import system_context

from angee.addons import available_addons
from angee.base.fields import StateField
from angee.base.models import AngeeManager, AngeeModel
from angee.platform import composed
from angee.platform.installer import InstallResult, addon_installer


class AddonManager(AngeeManager):
    """Manager owning the reflection table's reconcile and the install/uninstall flow."""

    def install(self, name: str) -> InstallResult:
        """Install an addon: validate it, edit ``settings.yaml``, then reflect ``pending``.

        Refuses a name no installed bundle or local addon provides — a marketplace
        (``REMOTE``) row is *known* but not materialised, so adding it to
        ``INSTALLED_APPS`` would brick the next boot — and otherwise delegates the
        ``settings.yaml`` edit to the :class:`~angee.platform.installer.AddonInstaller`
        and re-runs the reconcile so the board shows the new ``pending`` state at once
        (the addon itself composes on the next ``angee dev`` boot).
        """

        with system_context(reason="platform.addon.install"):
            if name not in available_addons(getattr(settings, "ANGEE_ADDON_DIRS", ())):
                return InstallResult.refusal(
                    name,
                    "install",
                    f"{name} is not available to install — no installed bundle or local addon provides it. "
                    "A marketplace addon must be materialised from its source first.",
                )
            result = addon_installer().install(name)
            if result.ok:
                self.reconcile_from_registry(router.db_for_write(self.model))
        return result

    def uninstall(self, name: str) -> InstallResult:
        """Uninstall an addon, refusing a forced (depended-on) one (Odoo "not uninstallable").

        The forced policy lives on the reflected row (:meth:`Addon.uninstall_block_reason`);
        this only resolves the row, relays its refusal, and otherwise delegates the
        ``settings.yaml`` edit and re-runs the reconcile so the board reflects the queued
        uninstall immediately.
        """

        with system_context(reason="platform.addon.uninstall"):
            using = router.db_for_write(self.model)
            row = self.using(using).filter(name=name).first()
            if row is not None and row.uninstall_block_reason:
                return InstallResult.refusal(name, "uninstall", row.uninstall_block_reason)
            result = addon_installer().uninstall(name)
            if result.ok:
                self.reconcile_from_registry(using)
        return result

    def reconcile_from_registry(self, using: str) -> None:
        """Converge the table to the composed app graph + available addons.

        A **state** reconcile, never a delete: an addon that leaves the project is
        marked ``REMOVED`` — its row, and so its history, is kept — rather than
        pruned. Scoped to the tier this reconcile owns (installed/local); rows of
        other tiers (the VCS marketplace ``platform_integrate_vcs`` contributes) are
        left untouched. Each present addon's row is a full overwrite so a state flip
        (enabled ↔ disabled) resets every reflected field. Runs under the caller's
        ``system_context`` (see ``signals.py``); routed through ``using`` and wrapped
        in one transaction like the sibling source reconciles, so a mid-loop failure
        never leaves the table half-converged.
        """

        facts = self._registry_facts()
        rows = self.using(using)
        owned = (Addon.Source.INSTALLED, Addon.Source.LOCAL)
        with transaction.atomic(using=using):
            rows.filter(source__in=owned).exclude(name__in=facts).update(
                state=Addon.State.REMOVED,
                forced=False,
                pending=False,
                model_count=0,
                field_count=0,
                resource_count=0,
                depends_on=[],
                model_labels=[],
                depended_by=[],
            )
            for name, defaults in facts.items():
                rows.update_or_create(name=name, defaults=defaults)

    @staticmethod
    def _registry_facts(desired: frozenset[str] | None = None) -> dict[str, dict[str, Any]]:
        """Build the complete reflected row for every available-or-enabled addon.

        ``desired`` is the set of ``settings.yaml`` ``INSTALLED_APPS`` roots (the
        install owner's view); defaults to the configured installer, which returns an
        empty set when ``settings.yaml`` is unreadable (bare test settings / inactive
        operator backend) so ``pending`` simply stays ``False``. An available-but-not-
        composed addon named in ``desired`` is ``pending`` (just installed, awaiting
        the next boot); a composed addon never is.
        """

        if desired is None:
            desired = frozenset(addon_installer().installed_app_names())
        rollups = {rollup.name: rollup for rollup in composed.addon_rollups()}  # enabled (composed)
        available = available_addons(getattr(settings, "ANGEE_ADDON_DIRS", ()))
        facts: dict[str, dict[str, Any]] = {}
        for name in sorted(set(rollups) | set(available)):
            rollup = rollups.get(name)
            ref = available.get(name)
            source = ref.source if ref is not None else Addon.Source.LOCAL
            if rollup is not None:
                facts[name] = {
                    "label": rollup.label,
                    "namespace": rollup.namespace,
                    "description": rollup.description,
                    "keywords": rollup.keywords,
                    "category": rollup.category,
                    "kind": rollup.kind,
                    "source": source,
                    "state": Addon.State.ENABLED,
                    "forced": rollup.forced,
                    # Composed but no longer a desired root → a queued *uninstall* (it
                    # leaves on the next boot). Scoped to roots: a non-root dependency is
                    # never in ``desired`` yet is not being uninstalled. The symmetric
                    # available-branch ``pending`` below is the queued *install*.
                    "pending": rollup.kind == Addon.Kind.CONSUMER and name not in desired,
                    "model_count": rollup.model_count,
                    "field_count": rollup.field_count,
                    "resource_count": rollup.resource_count,
                    "depends_on": rollup.depends_on,
                    "model_labels": rollup.model_labels,
                }
            else:  # available but not enabled — a complete row, every count zeroed.
                facts[name] = {
                    "label": name.rsplit(".", 1)[-1],
                    "namespace": name.split(".")[0],
                    # Metadata is read off the composed AppConfig's contract; an
                    # available-but-not-composed addon has no AppConfig yet, so it stays
                    # blank until it is composed (it gains a category/description then).
                    "description": "",
                    "keywords": [],
                    "category": "",
                    "kind": Addon.Kind.REQUIRED,
                    "source": source,
                    "state": Addon.State.DISABLED,
                    "forced": False,
                    "pending": name in desired,
                    "model_count": 0,
                    "field_count": 0,
                    "resource_count": 0,
                    "depends_on": [],
                    "model_labels": [],
                }
        # Reverse dependencies, inverted across the whole set so a paginated client
        # need not invert depends_on itself.
        depended_by: dict[str, list[str]] = {}
        for name, row in facts.items():
            for dependency in row["depends_on"]:
                depended_by.setdefault(dependency, []).append(name)
        for name, row in facts.items():
            row["depended_by"] = sorted(depended_by.get(name, ()))
        return facts


class Addon(AngeeModel):
    """The composed-runtime addon registry — local reflection, system-synced.

    Identity is ``name`` (e.g. ``angee.iam``) — the stable key the console
    cross-links on — not a sqid.
    """

    runtime = True

    objects = AddonManager()

    class Kind(models.TextChoices):
        """Whether the project chose this addon (root) or it came in as a dependency."""

        CONSUMER = "consumer", "Consumer"
        REQUIRED = "required", "Required"

    class Source(models.TextChoices):
        """Where the available addon resolved from."""

        INSTALLED = "installed", "Installed"  # an installed bundle's entry point (uv.lock)
        LOCAL = "local", "Local"  # an addon.toml under ANGEE_ADDON_DIRS
        REMOTE = "remote", "Remote"  # known from a VCS source, not materialised (platform_integrate_vcs)

    class State(models.TextChoices):
        """The addon's lifecycle in this project — reconciled, never deleted."""

        ENABLED = "enabled", "Enabled"  # composed into the app graph
        DISABLED = "disabled", "Disabled"  # available/known but not composed
        REMOVED = "removed", "Removed"  # was present, now gone from the env (kept as history)

    name = models.CharField(max_length=200, unique=True)
    label = models.CharField(max_length=100, blank=True, default="")
    namespace = models.CharField(max_length=100, blank=True, default="")
    # Manifest metadata (the addon's ``addon.toml``), reflected for the marketplace
    # board: the freeform ``category`` it groups by and the ``description``/``keywords``
    # the cards show. The contract owns these; the reconcile only mirrors them.
    description = models.TextField(blank=True, default="")
    keywords = models.JSONField(default=list, blank=True)
    category = models.CharField(max_length=100, blank=True, default="", db_index=True)
    kind = StateField(choices_enum=Kind, default=Kind.REQUIRED)
    source = StateField(choices_enum=Source, default=Source.INSTALLED)
    state = StateField(choices_enum=State, default=State.DISABLED, db_index=True)
    # Reflected from the composer's dependency closure (``AppGraph`` annotation): an
    # addon another installed addon depends on cannot be uninstalled (Odoo's
    # "not uninstallable"). Never re-derived here — the closure owner sets it.
    forced = models.BooleanField(default=False, db_index=True)
    # The desired-vs-actual diff: a root listed in ``settings.yaml`` ``INSTALLED_APPS``
    # but not yet composed into the running app graph (just installed, awaiting the
    # next ``angee dev`` boot) — the board's "to install" / pending-restart badge.
    pending = models.BooleanField(default=False, db_index=True)
    model_count = models.PositiveIntegerField(default=0)
    field_count = models.PositiveIntegerField(default=0)
    resource_count = models.PositiveIntegerField(default=0)
    depends_on = models.JSONField(default=list, blank=True)
    depended_by = models.JSONField(default=list, blank=True)
    model_labels = models.JSONField(default=list, blank=True)

    class Meta:
        """Django model options."""

        abstract = True
        ordering = ("name",)
        rebac_resource_type = "platform/addon"
        rebac_id_attr = "name"

    def __str__(self) -> str:
        """Return the addon name for Django displays."""

        return self.name

    @property
    def uninstall_block_reason(self) -> str:
        """Return why this addon cannot be uninstalled, or ``""`` when it may be.

        A forced (depended-on) addon — framework core, or anything another installed
        addon needs — cannot be uninstalled (Odoo's "not uninstallable"). The policy and
        its wording live on the row that carries ``forced`` (derived from the composer's
        dependency closure); the uninstall flow only relays this.
        """

        if self.forced:
            return f"{self.name} is required by another installed addon and cannot be uninstalled."
        return ""


class PlatformExplorer(AngeeModel):
    """Table-less REBAC type anchor for the platform introspection surface."""

    runtime = True

    class Meta:
        abstract = True
        managed = False
        rebac_resource_type = "platform/explorer"
