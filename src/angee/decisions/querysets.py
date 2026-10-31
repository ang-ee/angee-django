"""Decision admission sets, attention and immutable concern selection."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self

from django.apps import apps
from django.db.models import BooleanField, Exists, ExpressionWrapper, OuterRef, Q
from rebac import to_subject_ref

from angee.base.models.append_only import AppendOnlyQuerySet
from angee.base.permissions import actor_user_id
from angee.base.querysets import AngeeQuerySet
from angee.base.refs import generic_pointer_model

if TYPE_CHECKING:
    from angee.decisions.models import Decision


class DecisionQuerySet[T: Decision](AppendOnlyQuerySet[T], AngeeQuerySet[T]):
    def open_expression(self) -> ExpressionWrapper:
        return ExpressionWrapper(Q(verdict__isnull=True), output_field=BooleanField())

    def open(self) -> Self:
        return self.filter(verdict__isnull=True)

    def waiting_on(self, actor: Any) -> Self:
        """Open assignments, or unassigned questions this actor may answer.

        Retain the caller's read scope. An unassigned question delegates to the
        composed ``act`` permission rather than inventing a default assignee.
        """

        identity = actor_user_id(to_subject_ref(actor)) if actor is not None else None
        if identity is None:
            return self.none()
        actionable = (
            self.model.objects.db_manager(self.db).with_actor(actor).with_action("act")
            .scoped().filter(pk=OuterRef("pk"))
        )
        return self.open().filter(Q(assignees=identity) | (Q(assignees__isnull=True) & Q(Exists(actionable))))

    def open_for(self, *records: Any) -> Self:
        return self.open().for_records(*records)

    def for_records(self, *records: Any) -> Self:
        """Select decisions retaining any of the supplied canonical records."""

        link = apps.get_model("decisions", "DecisionRecord")
        concerns = Q(pk__in=[])
        for record in records:
            concerns |= link.record_filter(record)
        return self.filter(
            pk__in=link.system_queryset().using(self.db).filter(concerns).order_by().values("decision_id"),
        )

    def attention_expression(self, queryset: Any) -> Exists:
        model = generic_pointer_model(queryset.model)
        # Attention belongs to the readable record, regardless of who can answer
        # or read the question. Only the boolean crosses that permission boundary.
        return Exists(
            self.model.system_queryset().using(queryset.db)
            .open()
            .filter(
                records__content_type__app_label=model._meta.app_label,
                records__content_type__model=model._meta.model_name,
                records__object_id=OuterRef("pk"),
            )
        )



class DecisionRecordQuerySet(AppendOnlyQuerySet[Any], AngeeQuerySet):
    """Concern links are authored once with their decision."""
