"""Native attachment owners for storage."""

from __future__ import annotations

import logging
from typing import ClassVar

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from angee.base.models.audit import AuditMixin
from angee.base.models.merge_references import MergePolicy
from angee.iam.models.base import ProtectedDataModel
from angee.iam.models.refs import ProtectedRecordRefMixin
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.storage.managers import (
    FileAttachmentManager,
)

logger = logging.getLogger(__name__)


class FileAttachment(AuditMixin, ProtectedRecordRefMixin, HistoryMixin, ProtectedDataModel, concrete=True):
    """Polymorphic edge attaching one :class:`File` to any model row.

    Consumers attach explicitly through :meth:`FileAttachmentManager.attach` or
    declare an :class:`angee.base.fields.relations.GenericRelation` on the target for an
    ergonomic reverse accessor. Declare that reverse relation on the topmost
    REBAC-typed MTI ancestor that :func:`rebac.generic_target` keys the edge on,
    so the delete collector filters at the write content type (the placement
    invariant in :mod:`angee.base.refs`). Existing-edge access follows the file
    parent; creation follows the target relation the target's app declares in
    ``permissions.zed``.
    """

    record_display_paths: ClassVar[tuple[str, ...]] = ("label", "file_id")

    merge_policy = MergePolicy.MOVE
    merge_identity = (("file", "content_type", "object_id"),)

    sqid_prefix = "fat_"
    file = models.ForeignKey(
        "storage.File",
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    object_id = models.PositiveBigIntegerField()
    target = GenericForeignKey("content_type", "object_id")
    label = models.CharField(max_length=200, blank=True)

    objects = FileAttachmentManager()

    class Meta:
        """Django model options for file attachments."""

        abstract = True
        ordering = ("-created_at", "sqid")
        rebac_resource_type = "storage/file_attachment"
        constraints = (
            models.UniqueConstraint(
                fields=("file", "content_type", "object_id"),
                name="uq_storage_file_attachment_edge",
            ),
        )
        indexes = (models.Index(fields=("content_type", "object_id")),)

    def __str__(self) -> str:
        """Return the attachment label or a file-qualified fallback."""

        return self.label or f"attachment:{self.file_id}"
