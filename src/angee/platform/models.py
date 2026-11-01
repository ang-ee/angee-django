"""Source models for the platform addon.

The platform console reflects the runtime the composer already built. ``Addon`` is
that reflection made persistent: one row per composed/available addon, **converged
from the app registry after migrate** — the same reconcile Django runs for
``django_content_type`` / ``auth_permission`` (see ``signals.py``). It is therefore
authoritatively *derived*, never authored: ``settings.yaml`` (enabled) and
``uv.lock`` (available) remain the source of truth; this table is the queryable
mirror that backs the console.

The registry reconcile owns installed availability and running composition.
The same ``Addon`` table also holds remote catalogue rows;
``platform_integrate_vcs`` supplies their declarations and provenance. Each source
reconcile retains removed rows as history.

``PlatformExplorer`` stays a table-less REBAC type anchor for the schema explorer.
"""

from __future__ import annotations

from importlib import metadata
from typing import Any

from django.db import models

from angee.base.fields.state import StateField
from angee.iam.models.base import ProtectedModel
from angee.platform.discovery import (
    AddonCategory,
)
from angee.platform.managers import AddonChangeImpact as AddonChangeImpact
from angee.platform.managers import AddonChangePreview as AddonChangePreview
from angee.platform.managers import AddonContributedFieldInventory as AddonContributedFieldInventory
from angee.platform.managers import AddonDataInventory as AddonDataInventory
from angee.platform.managers import AddonManager as AddonManager
from angee.platform.managers import AddonModelInventory as AddonModelInventory
from angee.platform.managers import _preview_revision as _preview_revision


def get_addon_display_label(name: str, label: str) -> str:
    """Return the native Django label, falling back to the canonical addon name."""

    return label or name


class Addon(ProtectedModel, concrete=True):
    """The composed-runtime addon registry — local reflection, system-synced.

    Public identity is the inherited sqid. ``name`` (e.g. ``angee.iam``) is the
    unique native catalogue key used by composition and lifecycle actions.
    """

    objects = AddonManager()

    class AddonKind(models.TextChoices):
        """Whether the project chose this addon (root) or it came in as a dependency."""

        CONSUMER = "consumer", "Consumer"
        REQUIRED = "required", "Required"

    class AddonSource(models.TextChoices):
        """Where the available addon resolved from."""

        INSTALLED = "installed", "Installed"  # an installed bundle's entry point (uv.lock)
        LOCAL = "local", "Local"  # historical source-directory catalogue entry
        REMOTE = "remote", "Remote"  # known from a VCS source, not materialised (platform_integrate_vcs)

    class AddonState(models.TextChoices):
        """The addon's lifecycle in this project — reconciled, never deleted."""

        ENABLED = "enabled", "Enabled"  # composed into the app graph
        DISABLED = "disabled", "Disabled"  # available/known but not composed
        REMOVED = "removed", "Removed"  # was present, now gone from the env (kept as history)

    name = models.CharField(max_length=200, unique=True)
    label = models.CharField(max_length=100, blank=True, default="")
    namespace = models.CharField(max_length=100, blank=True, default="")
    # Package metadata (the addon's ``pyproject.toml``), reflected for the marketplace
    # board: the declared ``category`` it groups by and the ``description``/``keywords``
    # the cards show. The contract owns these; the reconcile only mirrors them.
    distribution = models.CharField(max_length=200, blank=True, default="")
    """Canonical native distribution identity; empty until metadata is refreshed."""

    requirements = models.JSONField(default=list, blank=True)
    """Native PEP 508 requirements, retained so other catalogue sources can resolve them."""

    description = models.TextField(blank=True, default="")
    keywords = models.JSONField(default=list, blank=True)
    category = models.CharField(max_length=100, blank=True, default="", db_index=True, choices=AddonCategory.choices)
    kind = StateField(choices_enum=AddonKind, default=AddonKind.REQUIRED)
    source = StateField(choices_enum=AddonSource, default=AddonSource.INSTALLED)
    state = StateField(choices_enum=AddonState, default=AddonState.DISABLED, db_index=True)
    # Snapshot of the loaded AppGraph's required-dependency annotation for display.
    # Commands consult that live annotation in AddonManager.change_preview.
    forced = models.BooleanField(default=False, db_index=True)
    # Desired-vs-loaded drift; reconcile_from_registry owns the pending rule.
    pending = models.BooleanField(default=False, db_index=True)
    model_count = models.PositiveIntegerField(default=0)
    field_count = models.PositiveIntegerField(default=0)
    resource_count = models.PositiveIntegerField(default=0)
    # Declared direct dependencies in every state, including the last known
    # manifest when removed.
    depends_on = models.JSONField(default=list, blank=True)
    # Inverse of current local/remote declarations, including disabled declarers.
    depended_by = models.JSONField(default=list, blank=True)
    model_labels = models.JSONField(default=list, blank=True)

    class Meta:
        """Django model options."""

        abstract = True
        constraints = (
            models.UniqueConstraint(
                fields=("distribution",), condition=~models.Q(distribution=""),
                name="%(app_label)s_%(class)s_distribution_uniq",
            ),
        )
        ordering = ("name",)
        rebac_resource_type = "platform/addon"

    def __str__(self) -> str:
        """Return the addon name for Django displays."""

        return self.name

    def get_display_label(self) -> str:
        """Return the display label for this catalogue entry."""

        return get_addon_display_label(self.name, self.label)

    def get_installed_version(self) -> str | None:
        """Read this distribution's installed version, or None for catalogue-only rows."""

        if not self.distribution:
            return None
        try:
            return metadata.version(self.distribution)
        except metadata.PackageNotFoundError:
            return None

    @staticmethod
    def reset_runtime_facts() -> dict[str, Any]:
        """Return fresh reset values for a catalogue row outside the loaded graph."""

        return {
            "forced": False,
            "pending": False,
            "model_count": 0,
            "field_count": 0,
            "resource_count": 0,
            "model_labels": [],
            "depended_by": [],
        }


class PlatformExplorer(ProtectedModel, concrete=True):
    """Table-less REBAC type anchor for the platform introspection surface."""


    class Meta:
        abstract = True
        managed = False
        rebac_resource_type = "platform/explorer"
