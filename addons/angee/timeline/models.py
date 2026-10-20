"""The record timeline's ledger: one row per occurrence, written by projection."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Self

from django.apps import apps
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from angee.identity import public_id_for
from angee.models import AngeeDataModel, MergePolicy
from angee.models.manager import AngeeUnscopedManager
from angee.models.query import AngeeUnscopedQuerySet
from angee.refs import RecordRefMixin, generic_pointer_target
from angee.timeline.kinds import timeline_kinds


class EventQuerySet(AngeeUnscopedQuerySet[Any]):
    """Read a record's ledger through each target owner's policy."""

    def for_record(self, record: models.Model) -> Self:
        """Every event on ``record``, by its canonical reference."""

        content_type, object_id = generic_pointer_target(record)
        return self.filter(record_content_type=content_type, record_object_id=object_id)

    def line_scope(self, actor: Any) -> models.Q:
        """A fold hides a row only while its target has a readable event here."""

        parents = self.model.objects.for_reader(actor).filter(
            record_content_type_id=models.OuterRef("record_content_type_id"),
            record_object_id=models.OuterRef("record_object_id"),
            target_model=models.OuterRef("fold_model"), target_pk=models.OuterRef("fold_pk"),
        ).exclude(pk=models.OuterRef("pk"))
        return ~models.Q(models.Exists(parents))

    def top_level(self, actor: Any) -> Self:
        return self.filter(self.line_scope(actor))

    def eligible_for(self, model: type[models.Model]) -> Self:
        return self.filter(kind__in=[kind.id for kind in timeline_kinds() if kind.eligible(model)])

    def for_reader(self, actor: Any) -> Self:
        """Apply every kind's read scope to entries, counts and folded children alike."""

        scope = models.Q(pk__in=())
        for kind in timeline_kinds():
            scope |= models.Q(kind=kind.id) & kind.readable(actor)
        return self.filter(scope)

    def waiting_on(self, actor: Any) -> models.Q:
        """The live, readable attention predicate; no recipient membership is stored."""

        scope = models.Q(pk__in=())
        for kind in timeline_kinds():
            scope |= models.Q(kind=kind.id) & kind.waiting_on(actor)
        return scope

    def needing(self, actor: Any) -> Self:
        return self.filter(self.waiting_on(actor))

    def folded_counts(self, page: Iterable[Any], actor: Any) -> dict[tuple[str, int], list[tuple[str, int]]]:
        """Count readable children under this record's page targets with one GROUP BY."""

        scope = models.Q(pk__in=())
        for event in page:
            scope |= models.Q(fold_model=event.target_model, fold_pk=event.target_pk)
        result: dict[tuple[str, int], list[tuple[str, int]]] = {}
        for model, pk, kind, count in self.filter(scope, ~self.line_scope(actor)).order_by().values_list(
            "fold_model", "fold_pk", "kind",
        ).annotate(count=models.Count("pk")).order_by("kind"):
            result.setdefault((model, pk), []).append((kind, count))
        return result


class EventManager(AngeeUnscopedManager.from_queryset(EventQuerySet)):  # type: ignore[misc]
    """The ledger's manager; reads compose the record gate with ``for_reader``."""


class Event(RecordRefMixin, AngeeDataModel):
    """One occurrence on a record's timeline, as its owning kind projects it.

    The ledger is an index, not the truth: every field is a projection of the
    owner's row (``target``), kept current by ``angee.timeline.events.sync_events``
    after committed changes. Readers need the record and the kind's target policy;
    this index carries no independent grant or attention state.
    """

    runtime = True
    sqid_prefix = "evt_"
    merge_policy = MergePolicy.MOVE
    on_target_delete = models.CASCADE
    accepts_any_record_target = True

    kind = models.CharField(max_length=64, db_index=True, editable=False)
    at = models.DateTimeField(db_index=True, editable=False)
    record_content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    record_object_id = models.PositiveBigIntegerField()
    record = GenericForeignKey("record_content_type", "record_object_id")
    target_model = models.CharField(max_length=150, editable=False)
    target_pk = models.PositiveBigIntegerField(editable=False)
    occurrence = models.CharField(max_length=64, default="1", editable=False)
    actor = models.TextField(blank=True, editable=False)
    actor_label = models.CharField(max_length=255, blank=True, editable=False)
    title = models.CharField(max_length=500, blank=True, editable=False)
    detail = models.CharField(max_length=500, blank=True, editable=False)
    fold_model = models.CharField(max_length=150, blank=True, editable=False)
    fold_pk = models.PositiveBigIntegerField(null=True, blank=True, editable=False)

    objects = EventManager()

    class Meta:
        abstract = True
        constraints = (
            models.UniqueConstraint(
                fields=("record_content_type", "record_object_id", "kind", "target_model", "target_pk", "occurrence"),
                name="timeline_event_occurrence",
            ),
        )
        indexes = (
            models.Index(
                fields=("record_content_type", "record_object_id", "-at", "kind", "-id"),
                name="timeline_event_record_order",
            ),
            models.Index(fields=("kind", "target_model", "target_pk"), name="timeline_event_target"),
            models.Index(fields=("fold_model", "fold_pk"), name="timeline_event_fold"),
        )

    merge_identity = (Meta.constraints[0].fields,)

    @property
    def key(self) -> str:
        """Public occurrence identity, derived from the sole database constraint."""

        return (f"{self.kind}:{self.target_model}:{self.target_pk}:"
                f"{self.record_content_type_id}:{self.record_object_id}:{self.occurrence}")

    @property
    def target_id(self) -> str:
        """Render the public identity through the target model's current codec."""

        return public_id_for(apps.get_model(self.target_model), self.target_pk)

    def merge_collapse(self, duplicate: Any) -> bool:
        """Two projections of the same occurrence are one disposable index entry."""

        return True

    def __str__(self) -> str:
        return f"{self.kind} · {self.title}"
