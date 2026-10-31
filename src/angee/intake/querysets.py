"""Request selection and first-request attribution expressions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self

from django.apps import apps
from django.db import models
from django.db.models.functions import Coalesce, NullIf
from rebac.relation_loading import relation_actor

from angee.base.permissions import actor_user_id
from angee.provenance.contrib.angee.querysets import AngeeHistoryQuerySet

if TYPE_CHECKING:
    from angee.intake.models import Need


class NeedQuerySet[NeedT: Need](AngeeHistoryQuerySet[NeedT]):
    """Request collections follow their stored target, including project tasks."""

    def for_project(self, project: models.Model) -> Self:
        """Include direct requests and requests on the project's tasks."""

        return self.filter(models.Q(project=project) | models.Q(task__project=project))


def task_requester_rows(task: str = "pk") -> models.QuerySet[Any]:
    """A task's requests in attribution order; the first is the one the task shows as filed by.

    ``task`` names the outer query's column holding the task id.
    """

    return (
        apps.get_model("intake", "Need")
        ._base_manager.filter(
            task_id=models.OuterRef(task),
        )
        .order_by("created_at", "pk")
    )


def task_requester_name() -> models.Subquery:
    """The name a task shows for its requester: the first request's party, else the name it claimed."""

    blank = models.Value("", output_field=models.TextField())
    return models.Subquery(
        task_requester_rows()
        .annotate(
            name=Coalesce(
                NullIf(models.F("party__display_name"), blank),
                NullIf(models.F("claimed_name"), blank),
                output_field=models.TextField(),
            )
        )
        .values("name")[:1],
        output_field=models.TextField(null=True),
    )


def _requester_name_filter(queryset: models.QuerySet[Any]) -> models.Subquery:
    """Filter on the first request's shown name, never its email or a later request's name."""

    del queryset
    return task_requester_name()


def _requested_by_viewer_filter(queryset: models.QuerySet[Any]) -> models.Expression:
    """Whether the first Need's party is the viewer's person (MTI child), regardless of later Needs.

    It compares the actor with itself only, so it reveals nothing about anyone else's requests;
    an actor without a user matches nothing. REBAC's broader requester relation
    can include later approved requests; this filter only attributes the first.
    """

    user_id = actor_user_id(relation_actor(queryset))
    if user_id is None:
        return models.Value(False, output_field=models.BooleanField())
    # Uncorrelated, so the database evaluates it once from the viewer's own requests.
    filed = (
        apps.get_model("intake", "Need")
        ._base_manager.filter(
            party__person__user=user_id,
            pk=models.Subquery(task_requester_rows("task_id").values("pk")[:1]),
        )
        .values("task_id")
    )
    return models.Case(
        models.When(pk__in=filed, then=models.Value(True)),
        default=models.Value(False),
        output_field=models.BooleanField(),
    )


