"""Native folder owners for storage."""

from __future__ import annotations

import logging
from typing import Any

from django.core.exceptions import (
    ValidationError,
)
from django.db import models
from django.db.models import Q

from angee.base.fields.state import StateField
from angee.base.models.base import AngeeDataModel
from angee.base.models.hierarchy import HierarchyMixin
from angee.base.models.ownership import OwnerMixin
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.storage.managers import (
    FolderManager,
)

logger = logging.getLogger(__name__)


class Folder(HierarchyMixin, OwnerMixin, HistoryMixin, AngeeDataModel, concrete=True):
    """Tree node inside a drive, or a per-user smart folder.

    A real folder has a ``drive`` and filesystem-style uniqueness on
    ``(drive, parent, name)``. A smart folder has no drive: it belongs to one
    ``owner``, is flagged ``is_virtual``, and surfaces files through a backing
    query — Trash lists ``File.objects.trashed()`` — so it stores no edges.
    """

    sqid_prefix = "fld_"
    hierarchy_scope_fields = ("drive",)
    owner_container = "drive"

    objects = FolderManager()

    def insert_owner_default(self) -> Any:
        """A folder in a drive belongs to the drive; only a smart folder is a user's own."""

        if self.drive_id is not None:
            return None
        return super().insert_owner_default()

    class SmartKind(models.TextChoices):
        """Backing queries a smart folder can surface."""

        TRASH = "trash", "Trash"

    drive = models.ForeignKey(
        "storage.Drive",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="folders",
    )
    # A single path segment, mirrored from an external source; one observed iOS
    # backup cache-directory segment exceeded 200 characters. Match File.filename.
    name = models.CharField(max_length=512)
    description = models.TextField(blank=True)
    is_virtual = models.BooleanField(default=False, db_index=True, editable=False)
    smart_kind = StateField(
        choices_enum=SmartKind,
        null=True,
        blank=True,
        editable=False,
    )

    class Meta(HierarchyMixin.Meta):
        """Django model options for folders."""

        abstract = True
        ordering = ("name", "sqid")
        rebac_resource_type = "storage/folder"
        constraints = (
            models.UniqueConstraint(
                fields=("drive", "parent", "name"),
                condition=Q(is_virtual=False),
                name="uniq_storage_folder_drive_parent_name",
            ),
            # SQL unique treats NULLs as distinct, so root folders (NULL
            # parent) need their own uniqueness arm.
            models.UniqueConstraint(
                fields=("drive", "name"),
                condition=Q(is_virtual=False) & Q(parent__isnull=True),
                name="uniq_storage_folder_drive_root_name",
            ),
            models.UniqueConstraint(
                fields=("owner", "smart_kind"),
                condition=Q(is_virtual=True, smart_kind__isnull=False),
                name="uniq_storage_folder_owner_smart_kind",
            ),
            models.CheckConstraint(
                condition=Q(is_virtual=True) | Q(drive__isnull=False),
                name="storage_folder_real_requires_drive",
            ),
        )

    def __str__(self) -> str:
        """Return the folder name."""

        return self.name

    def clean(self) -> None:
        """Validate the drive boundary and native hierarchy before a form save."""

        super().clean()
        self._validate_drive()
        parent = self._hierarchy_parent()
        self._reject_cross_scope_parent(parent)
        self._reject_cycle(parent)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Validate folder kinds; native hierarchy owns parent and drive moves."""

        update_fields = kwargs.get("update_fields")
        if update_fields is None or {
            "drive",
            "drive_id",
            "parent",
            "parent_id",
            "is_virtual",
        }.intersection(update_fields):
            self._validate_drive()
        super().save(*args, **kwargs)

    def _validate_drive(self) -> None:
        if self.is_virtual:
            if self.parent_id is not None:
                raise ValidationError({"parent": "A smart folder must be a root."})
        elif self.drive_id is None:
            raise ValidationError({"drive": "A folder requires a drive."})
