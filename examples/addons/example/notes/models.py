"""Source models for the notes addon."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.db import models, transaction

from angee.base.fields import StateField
from angee.base.mixins import (
    AuditMixin,
    HistoryMixin,
    RevisionMixin,
    SqidMixin,
)
from angee.base.models import AngeeModel
from angee.messaging.models import ThreadedModelMixin


class Note(SqidMixin, AuditMixin, ThreadedModelMixin, AngeeModel, HistoryMixin, RevisionMixin):
    """A short note used to exercise backend composition.

    Metadata changes are audited through ``history``; the ``body`` field is
    versioned through ``revisions`` so edits can be rolled back.
    """

    runtime = True

    revisioned_fields = ("body",)

    sqid_prefix = "nte_"

    class Status(models.TextChoices):
        """Lifecycle states a note moves through."""

        DRAFT = "draft", "Draft"
        IN_REVIEW = "in_review", "In Review"
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    title = models.CharField(max_length=160)
    body = models.TextField(blank=True, default="")
    word_count = models.PositiveIntegerField(default=0, db_index=True)
    status = StateField(choices_enum=Status, default=Status.DRAFT)
    tags = models.JSONField(blank=True, default=list)
    is_starred = models.BooleanField(default=False, db_index=True)
    reminder_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        """Django model options."""

        abstract = True
        ordering = ("-updated_at", "title", "sqid")
        rebac_resource_type = "notes/note"
        rebac_id_attr = "sqid"

    def __str__(self) -> str:
        """Return the note title for Django displays."""

        return self.title

    @staticmethod
    def count_words(body: str) -> int:
        """Return the number of whitespace-delimited words in ``body``."""

        return len((body or "").split())

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the current number of whitespace-delimited body words."""

        self.word_count = self.count_words(self.body)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            field_names = set(update_fields)
            if "body" in field_names:
                field_names.add("word_count")
                field_names.add("updated_at")
                kwargs["update_fields"] = field_names
        super().save(*args, **kwargs)

    def publication_summary(self) -> dict[str, str]:
        """Validate publication readiness and return the safe workflow projection."""

        errors: dict[str, str] = {}
        if not self.title.strip():
            errors["title"] = "A note title is required for publication."
        if not self.body.strip():
            errors["body"] = "Note content is required for publication."
        if self.status != self.Status.IN_REVIEW:
            errors["status"] = "A note must be in review before publication."
        if errors:
            raise ValidationError(errors)
        return {
            "id": str(self.sqid),
            "title": self.title,
            "status": str(self.status),
        }

    def publish(self) -> dict[str, str]:
        """Publish a ready note through its normal audited save path."""

        with transaction.atomic():
            current = type(self)._base_manager.select_for_update().get(pk=self.pk)
            current.publication_summary()
            current.status = self.Status.ACTIVE
            current.save(update_fields={"status"})
            return {
                "id": str(current.sqid),
                "title": current.title,
                "status": str(current.status),
            }
