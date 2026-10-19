"""The record timeline's ledger: one row per occurrence, written by projection."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from angee.models import AngeeDataModel, MergePolicy
from angee.models.manager import AngeeUnscopedManager
from angee.models.query import AngeeUnscopedQuerySet
from angee.refs import RecordRefMixin, generic_pointer_target


class EventQuerySet(AngeeUnscopedQuerySet[Any]):
    """Structural reads over the ledger; the caller authorizes the record."""

    def for_record(self, record: models.Model) -> Any:
        """Every event on ``record``, by its canonical reference."""

        content_type, object_id = generic_pointer_target(record)
        return self.filter(record_content_type=content_type, record_object_id=object_id)

    def top_level(self) -> Any:
        """Events that are lines of their own; folded children hang under their parent."""

        return self.filter(parent__isnull=True)

    def needing(self, user: Any) -> Any:
        return self.filter(needs=user)


class EventManager(AngeeUnscopedManager.from_queryset(EventQuerySet)):  # type: ignore[misc]
    """The ledger's manager: permission-naive, scoped by the record the caller read."""


class Event(RecordRefMixin, AngeeDataModel):
    """One occurrence on a record's timeline, as its owning kind projects it.

    The ledger is an index, not the truth: every field is a projection of the
    owner's row (``target``), kept current by ``angee.timeline.events.sync_events``
    on that row's saves. Reads are authorized by reading the record, like
    statements and history; the row carries no policy of its own.
    """

    runtime = True
    sqid_prefix = "evt_"
    merge_policy = MergePolicy.MOVE
    on_target_delete = models.CASCADE
    accepts_any_record_target = True

    key = models.CharField(max_length=255, unique=True, editable=False)
    kind = models.CharField(max_length=64, db_index=True, editable=False)
    at = models.DateTimeField(db_index=True, editable=False)
    record_content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    record_object_id = models.PositiveBigIntegerField()
    record = GenericForeignKey("record_content_type", "record_object_id")
    target_model = models.CharField(max_length=150, editable=False)
    target_id = models.CharField(max_length=64, editable=False)
    actor = models.TextField(blank=True, editable=False)
    actor_label = models.CharField(max_length=255, blank=True, editable=False)
    title = models.CharField(max_length=500, blank=True, editable=False)
    detail = models.CharField(max_length=500, blank=True, editable=False)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="children")
    needs = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="+")

    objects = EventManager()

    class Meta:
        abstract = True
        indexes = (
            models.Index(
                fields=("record_content_type", "record_object_id", "-at", "kind", "-id"),
                name="timeline_event_record_order",
            ),
            models.Index(fields=("kind", "target_model", "target_id"), name="timeline_event_target"),
        )

    def __str__(self) -> str:
        return f"{self.kind} · {self.title}"
