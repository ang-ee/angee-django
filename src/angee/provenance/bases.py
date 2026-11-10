"""Leaf model bases shared by history capture and retained statements."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from django.db import models

from angee.provenance.context import changeset_model_label

if TYPE_CHECKING:
    from angee.provenance.history import ModelHistory


class ChangesetHistory(models.Model):
    """The writing changeset's foreign key on historical rows and retained statements."""

    provenance_history: ClassVar[ModelHistory | None] = None
    """Capture declaration bound by ModelHistory's native model factory."""

    changeset = models.ForeignKey(
        changeset_model_label(), null=True, blank=True, on_delete=models.PROTECT, related_name="+",
    )

    class Meta:
        abstract = True
