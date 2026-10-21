"""Closed changesets selected through the history owner's document membership."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.db import models
from django.db.models.functions import Cast
from rebac import SubjectRef, resolve_subjects

from angee.models import record_history
from angee.timeline.kinds import Presentation, RecordIdentity, TimelineKind, timeline_subject


class ChangeKind(TimelineKind):
    id = "provenance.change"
    sequence = 30
    source_models = ("provenance.Changeset",)
    explicit_publication = True
    at = models.F("occurred_at")

    def eligible(self, model: type[models.Model]) -> bool:
        return record_history(model) is not None and timeline_subject(model)

    def queryset(self, record: Any, actor: Any) -> models.QuerySet:
        model = apps.get_model("provenance", "Changeset")
        return model.objects.sudo(reason="provenance.record_history").touching(record).filter(
            state=model.State.CLOSED,
        ).scoped()

    def fold(self, record: Any) -> tuple[Any, Any]:
        integer_key = models.Q(origin_object_id__regex=r"^[0-9]{1,18}$") | models.Q(
            origin_object_id__regex=r"^[0-9]{19}$", origin_object_id__lte="9223372036854775807",
        )
        return models.F("origin_content_type_id"), models.Case(
            models.When(integer_key, then=Cast("origin_object_id", models.BigIntegerField())),
            output_field=models.BigIntegerField(),
        )

    def present(self, rows: Iterable[Any]) -> dict[int, Presentation]:
        rows = list(rows)
        actors = {row.actor: SubjectRef.parse(row.actor) for row in rows if row.actor}
        resolved = resolve_subjects(actor for actor in actors.values()
                                    if actor.subject_id not in ("", "*") and not actor.optional_relation)
        labels = {value: str(resolved.get(actor, actor)) for value, actor in actors.items()}
        return {row.pk: Presentation(
            f"{row.get_action_display()} · {row.get_activity_display()}", row.reason_display, labels.get(row.actor, ""),
        ) for row in rows}

    def records(self, row: Any) -> Iterable[RecordIdentity]:
        if row.state == row.State.CLOSED:
            yield from ((model, pk) for model, pk in type(row).objects.filter(pk=row.pk).records_touched()
                        if self.eligible(model))


def timeline_kinds() -> tuple[TimelineKind, ...]:
    return (ChangeKind(),)
