"""Decision admission sets, attention and immutable concern selection."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db.models import BooleanField, Case, Exists, ExpressionWrapper, ManyToManyField, OuterRef, Q, Value, When
from rebac import permission_expression, to_subject_ref

from angee.base.refs import generic_pointer_model
from angee.iam.models.append_only import ProtectedAppendOnlyQuerySet
from angee.iam.permissions import actor_user_id
from angee.iam.querysets import ProtectedQuerySet

if TYPE_CHECKING:
    from angee.decisions.models import Decision


class DecisionQuerySet[T: Decision](ProtectedAppendOnlyQuerySet[T], ProtectedQuerySet[T]):
    def open_expression(self) -> ExpressionWrapper:
        return ExpressionWrapper(Q(verdict__isnull=True), output_field=BooleanField())

    def open(self) -> Self:
        return self.filter(verdict__isnull=True)

    def waiting_on_expression(self, actor: Any) -> Case | Value:
        """Project open assignments or an unassigned question this actor may answer.

        This is attention, not row admission; the containing queryset retains
        its read scope. Assignment existence never multiplies the outer rows.
        """

        subject = to_subject_ref(actor) if actor is not None else None
        identity = actor_user_id(subject)
        if identity is None:
            return Value(False, output_field=BooleanField())
        assert subject is not None
        field = self.model._meta.get_field("assignees")
        assert isinstance(field, ManyToManyField)
        through = field.remote_field.through
        assert through is not None
        assignments = through._default_manager.using(self.db).filter(
            **{field.m2m_field_name(): OuterRef("pk")},
        )
        assigned = Exists(assignments.filter(**{field.m2m_reverse_field_name(): identity}))
        unassigned = ~Exists(assignments)
        actionable = permission_expression(self.model, "act", actor=subject)
        return Case(
            When(self.open_expression() & (assigned | (unassigned & actionable)), then=Value(True)),
            default=Value(False), output_field=BooleanField(),
        )

    def waiting_on(self, actor: Any) -> Self:
        """Retain the caller's read scope while selecting its attention rows."""
        subject = to_subject_ref(actor) if actor is not None else None
        if actor_user_id(subject) is None:
            return self.none()
        return self.filter(self.waiting_on_expression(subject))

    def open_for(self, *records: Any) -> Self:
        return self.open().for_records(*records)

    def for_records(self, *records: Any) -> Self:
        """Select decisions retaining supplied canonical records."""

        link = apps.get_model("decisions", "DecisionRecord")
        concerns = Q(pk__in=[])
        for record in records:
            concerns |= link.record_filter(record)
        return self.filter(
            pk__in=link._base_manager.using(self.db)
            .filter(concerns).order_by().values("decision_id"),
        )

    def attention_expression(self, queryset: Any) -> Exists:
        model = generic_pointer_model(queryset.model)
        content_type_id = ContentType.objects.db_manager(queryset.db).filter(
            app_label=model._meta.app_label, model=model._meta.model_name,
        ).values("pk")[:1]
        # Attention belongs to the readable record, regardless of who can answer
        # or read the question. Only the boolean crosses that permission boundary.
        rows = self.model.system_queryset().using(queryset.db)
        return Exists(
            rows.open().filter(
                records__content_type_id__in=content_type_id,
                records__object_id=OuterRef("pk"),
            )
        )


class DecisionRecordQuerySet(ProtectedAppendOnlyQuerySet[Any], ProtectedQuerySet):
    """Concern links are authored once with their decision."""
