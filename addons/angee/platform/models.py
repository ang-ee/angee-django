"""Source models for the platform addon.

The platform console reflects the runtime the composer already built. ``Addon`` is
that reflection made persistent: one row per composed/available addon, **converged
from the app registry after migrate** — the same reconcile Django runs for
``django_content_type`` / ``auth_permission`` (see ``signals.py``). It is therefore
authoritatively *derived*, never authored: ``settings.yaml`` (enabled) and
``uv.lock`` (available) remain the source of truth; this table is the queryable
mirror that backs the console.

Scope is deliberately **local**: which addons are *available* (installed bundles +
local ``addon.toml``) and *enabled* (composed). The remote marketplace — addons
known from VCS provenance but not materialised — is **not** here; the
``platform_integrate_vcs`` addon extends ``Addon`` with that.

``PlatformExplorer`` stays a table-less REBAC type anchor for the schema explorer.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import models

from angee.addons import available_addons
from angee.base.fields import StateField
from angee.base.models import AngeeManager, AngeeModel
from angee.platform import composed


class AddonManager(AngeeManager):
    """Manager owning the reflection table's reconcile."""

    def reconcile_from_registry(self, using: str) -> None:
        """Converge the table to the composed app graph + available addons.

        The content-type reconcile: create-missing, update, prune-stale — a **full
        overwrite per row** so a state flip (enabled → available) resets every
        reflected field rather than leaving stale counts. Runs under the caller's
        ``system_context`` (see ``signals.py``); routed through ``using``.
        """

        facts = self._registry_facts()
        rows = self.using(using)
        stale = set(rows.values_list("name", flat=True)) - set(facts)
        if stale:
            rows.filter(name__in=stale).delete()
        for name, defaults in facts.items():
            rows.update_or_create(name=name, defaults=defaults)

    @staticmethod
    def _registry_facts() -> dict[str, dict[str, Any]]:
        """Build the complete reflected row for every available-or-enabled addon."""

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
                    "kind": rollup.kind,
                    "source": source,
                    "enabled": True,
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
                    "kind": Addon.Kind.REQUIRED,
                    "source": source,
                    "enabled": False,
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

    name = models.CharField(max_length=200, unique=True)
    label = models.CharField(max_length=100, blank=True, default="")
    namespace = models.CharField(max_length=100, blank=True, default="")
    kind = StateField(choices_enum=Kind, default=Kind.REQUIRED)
    source = StateField(choices_enum=Source, default=Source.INSTALLED)
    enabled = models.BooleanField(default=False, db_index=True)
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


class PlatformExplorer(AngeeModel):
    """Table-less REBAC type anchor for the platform introspection surface."""

    runtime = True

    class Meta:
        abstract = True
        managed = False
        rebac_resource_type = "platform/explorer"
