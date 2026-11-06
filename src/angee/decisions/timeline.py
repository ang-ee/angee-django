"""Decisions on records, folding through their owner's requesting references."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models.functions import Coalesce
from django_cte import CTE, with_cte

from angee.base.refs import generic_pointer_model
from angee.iam.identity import user_label_queryset
from angee.timeline.kinds import Presentation, RecordIdentity, TimelineKind, actor_label

KIND = "decisions.decision"


class DecisionKind(TimelineKind):
    id = KIND
    sequence = 20
    source_models = {
        "decisions.Decision": ("verdict", "verdict_values", "answered_by", "answered_at", "kind"),
    }
    previous_source_models = {"decisions.Decision": ()}

    def eligible(self, model: type[models.Model]) -> bool:
        return True

    def queryset(self, record: Any, actor: Any) -> models.QuerySet:
        return self.source(record, actor)[0]

    def source(self, record: Any, actor: Any) -> tuple[models.QuerySet, tuple[Any, Any] | None]:
        """Share structural requester choices; admit decisions in the outer read."""
        decision = apps.get_model("decisions", "Decision")
        readable = decision.objects.with_actor(actor)
        requester: CTE | None = None
        fold: tuple[Any, Any] | None = None
        requesters = decision.requester_querysets()
        if requesters:
            link = apps.get_model("decisions", "DecisionRecord")
            concerns = link._base_manager.using(readable.db).filter(link.record_filter(record)).order_by().values(
                "decision_id",
            )
            candidates = models.Q(pk__in=concerns)
            choices: dict[str, Any] = {}
            types: list[int] = []
            local_fields: list[str] = []
            for priority, source in enumerate(requesters):
                field = f"_requester_{priority}"
                types.append(ContentType.objects.get_for_model(source.model).pk)
                source = source.using(readable.db)
                order: tuple[Any, ...] = ("pk",)
                if generic_pointer_model(source.model) is generic_pointer_model(type(record)):
                    local_ids = tuple(sorted(set(source.filter(
                        pk=record.pk, _decision_id__isnull=False,
                    ).order_by().values_list("_decision_id", flat=True))))
                    candidates |= models.Q(pk__in=local_ids)
                    order = (models.Case(models.When(pk=record.pk, then=0), default=1), "pk")
                    local_fields.append(field)
                choices[field] = models.Subquery(
                    source.filter(_decision_id=models.OuterRef("pk")).order_by(*order).values("pk")[:1],
                )
            # The CTE supplies record-bounded identities and fold choices. The
            # outer Decision read admits its rows before any choice is exposed.
            base: models.QuerySet[Any] = decision._base_manager.using(readable.db).filter(candidates)
            requester = CTE(base.annotate(**choices).values("pk", *choices),
                            name="timeline_requesters")
            requested: models.QuerySet = models.QuerySet(
                model=decision, query=requester.queryset().query.clone(), using=readable.db,
            )
            rows = readable.filter(pk__in=requested.values("pk")).scoped()
            parent = requested.filter(pk=models.OuterRef("pk"))
            local = models.Q(pk__in=[])
            for field in local_fields:
                local |= models.Q(**{field: record.pk})
            local_requester = (models.Exists(parent.filter(local)) if local_fields else
                               models.Value(False, output_field=models.BooleanField()))
            fold_type = models.Case(*(
                models.When(**{f"_requester_{priority}__isnull": False}, then=models.Value(content_type))
                for priority, content_type in enumerate(types)
            ), default=models.Value(None), output_field=models.IntegerField())
            fold_pk = Coalesce(*(models.F(f"_requester_{priority}") for priority in range(len(types))),
                               output_field=models.BigIntegerField())
            fold = (
                models.Case(
                    models.When(local_requester, then=models.Value(None)),
                    default=models.Subquery(parent.annotate(_fold_type=fold_type).values("_fold_type")[:1]),
                    output_field=models.IntegerField(),
                ),
                models.Case(
                    models.When(local_requester, then=models.Value(None)),
                    default=models.Subquery(parent.annotate(_fold_pk=fold_pk).values("_fold_pk")[:1]),
                    output_field=models.BigIntegerField(),
                ),
            )
        else:
            rows = readable.for_records(record).scoped()
        rows = rows.select_related("answered_by").prefetch_related(
            models.Prefetch("assignees", queryset=user_label_queryset()),
        )
        return with_cte(requester, select=rows) if requester is not None else rows, fold

    def waiting_on_expression(self, rows: Any, actor: Any) -> models.Expression:
        return rows.waiting_on_expression(actor)

    def present(self, rows: Iterable[Any]) -> dict[int, Presentation]:
        result = {}
        for row in rows:
            names = ", ".join(actor_label(user) for user in row.assignees.all())
            detail = (f"Waiting on {names}" if names else "Waiting") if row.is_open else row.verdict_label
            result[row.pk] = Presentation(row.kind_label, detail, actor_label(row.answered_by))
        return result

    def records(self, row: Any) -> Iterable[RecordIdentity]:
        links = row.records.model.system_queryset().using(row._state.db).filter(
            decision_id=row.pk,
        ).select_related("content_type")
        for link in links:
            if (model := link.content_type.model_class()) is not None:
                yield model, link.object_id
        yield from ((type(record), record.pk) for record in row.requesting_records())


def timeline_kinds() -> tuple[TimelineKind, ...]:
    return (DecisionKind(),)
