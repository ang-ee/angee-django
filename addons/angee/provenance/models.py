"""Changeset metadata; concrete classes are emitted by the composer."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, ClassVar

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models, transaction
from django.utils import timezone
from rebac import RebacMixin, RelationshipTuple, SubjectRef, to_object_ref, write_relationships

from angee.base.mixins import SqidMixin
from angee.base.models import AngeeManager
from angee.base.refs import MergePolicy, RecordRefMixin


class Changeset(RecordRefMixin, SqidMixin, RebacMixin):
    """One logical write's attribution and touched-model index; never historized.

    ``actor`` is the native SubjectRef wire representation. Its relationship
    tuple is a permission projection, inserted atomically through REBAC's trusted
    proposed-relationships contract. History user stamps derive from the column.
    """

    runtime = True
    sqid_prefix = "cst_"
    merge_policy: ClassVar[MergePolicy] = MergePolicy.KEEP
    objects = AngeeManager()

    actor = models.TextField(blank=True, editable=False)
    action = models.CharField(max_length=1, choices=(("C", "Create"), ("U", "Update"), ("D", "Delete")), default="U")
    activity = models.CharField(max_length=64, default="manual")
    change_reason = models.TextField(blank=True)
    correlation_id = models.CharField(max_length=255, blank=True, db_index=True)
    idempotency_key = models.CharField(max_length=255, blank=True, db_index=True)
    origin_content_type = models.ForeignKey(
        ContentType, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    origin_object_id = models.CharField(max_length=1024, null=True, blank=True)
    origin = GenericForeignKey("origin_content_type", "origin_object_id")
    occurred_at = models.DateTimeField(default=timezone.now)
    recorded_at = models.DateTimeField(default=timezone.now, editable=False)
    state = models.CharField(max_length=16, choices=(("open", "Open"), ("closed", "Closed")), default="open")
    outcome = models.CharField(
        max_length=16,
        choices=(("success", "Success"), ("failure", "Failure")),
        blank=True,
    )
    hash = models.CharField(max_length=64, blank=True, editable=False)
    counts = models.JSONField(default=dict, blank=True, editable=False)
    touched = models.JSONField(default=list, blank=True, editable=False)

    class Meta:
        abstract = True
        swappable = "ANGEE_CHANGESET_MODEL"
        rebac_resource_type = "provenance/changeset"
        ordering = ("-recorded_at", "-pk")

    def proposed_relationships(self, *, using: str | None = None) -> Mapping[str, Iterable[SubjectRef | models.Model]]:
        """Promise the actor tuple that this owner persists immediately after insert."""

        return {"actor": (SubjectRef.parse(self.actor),)} if self.actor else {}

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the native actor permission fact in the insert transaction."""

        inserting = self._state.adding
        if self.actor:
            SubjectRef.parse(self.actor)
        with transaction.atomic():
            super().save(*args, **kwargs)
            if inserting and self.actor:
                write_relationships(
                    [
                        RelationshipTuple(
                            resource=to_object_ref(self),
                            relation="actor",
                            subject=SubjectRef.parse(self.actor),
                        ),
                    ]
                )
