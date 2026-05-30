"""Source models owned by the resources subpackage."""

from __future__ import annotations

from django.db import models

from angee.base.models import AngeeModel
from angee.base.resources.managers import ResourceManager
from angee.base.resources.tiers import ResourceTier


class Resource(AngeeModel):
    """Ledger row for idempotent resource imports."""

    Tier = ResourceTier

    source_addon = models.CharField(max_length=200)
    source_path = models.CharField(max_length=300)
    tier = models.CharField(max_length=40, choices=ResourceTier.choices)
    xref = models.CharField(max_length=160)
    content_hash = models.CharField(max_length=71)
    target_model = models.CharField(max_length=120)
    target_id = models.CharField(max_length=120, blank=True, default="")
    loaded_at = models.DateTimeField(auto_now=True)

    objects = ResourceManager()

    class Meta:
        """Django model options."""

        abstract = True
        ordering = ("source_addon", "source_path", "xref", "target_model")
        constraints = (
            models.UniqueConstraint(
                fields=("source_addon", "source_path", "xref", "target_model"),
                name="%(app_label)s_resource_source_target",
            ),
        )
