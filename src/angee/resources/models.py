"""Abstract source model for the resource import ledger."""

from __future__ import annotations

from django.db import models

from angee.base.fields import StateField
from angee.base.models import AngeeModel
from angee.resources.managers import ResourceManager
from angee.resources.tiers import ResourceTier


class Resource(AngeeModel):
    """Ledger row linking source resource xrefs to imported targets."""

    Tier = ResourceTier
    """Tier enum used by resource declarations and ledger rows."""

    source_addon = models.CharField(max_length=200)
    """Dotted addon name that declared the source resource row."""

    source_path = models.CharField(max_length=300)
    """Resource path or URL that supplied the source row."""

    tier = StateField(choices_enum=ResourceTier)
    """Tier selected when the source row was loaded."""

    xref = models.CharField(max_length=160)
    """Addon-local external reference for the source row."""

    content_hash = models.CharField(max_length=71)
    """Hash of model field values from the source row."""

    target_model = models.CharField(max_length=120)
    """Django model label for the imported target row."""

    target_id = models.CharField(max_length=120, blank=True, default="")
    """Public identifier for the imported target row."""

    loaded_at = models.DateTimeField(auto_now=True)
    """Timestamp when the ledger row was last written."""

    objects = ResourceManager()
    """Manager with validate, load, and diff operations."""

    class Meta:
        """Django model options for the abstract resource ledger."""

        abstract = True
        ordering = ("source_addon", "source_path", "xref", "target_model")
        constraints = (
            models.UniqueConstraint(
                fields=("source_addon", "xref"),
                name="%(app_label)s_resource_addon_xref",
            ),
        )
