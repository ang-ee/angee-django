"""Chainable proposal-round scopes and guarded collection writes."""

from __future__ import annotations

from collections.abc import Collection, Iterable
from typing import TYPE_CHECKING, Any, Self

from django.core.exceptions import ValidationError
from django.db import models

from angee.base.querysets import AngeeQuerySet, AngeeUnscopedQuerySet, ClosedUpdateQuerySet
from angee.proposals.choices import RoundStatus

if TYPE_CHECKING:
    from angee.projects.models import Project
    from angee.proposals.models import Round


class _RoundRequesterWrites(ClosedUpdateQuerySet):
    """Reserve requester identity changes for the round's locked instance owner."""

    def update(self, **kwargs: Any) -> int:
        if {"requester_party", "requester_party_id"}.intersection(kwargs):
            raise ValidationError({"requester_party": "Change requester identity through the round instance."})
        return self.owner_update(**kwargs)

    def bulk_update(self, objs: Any, fields: Any, *args: Any, **kwargs: Any) -> int:
        """Keep native batch edits subject to the requester update guard."""

        return super(ClosedUpdateQuerySet, self).bulk_update(objs, fields, *args, **kwargs)

    def bulk_create(
        self,
        objs: Iterable[Any],
        batch_size: int | None = None,
        ignore_conflicts: bool = False,
        update_conflicts: bool = False,
        update_fields: Collection[str] | None = None,
        unique_fields: Collection[str] | None = None,
    ) -> list[Any]:
        if update_conflicts and {"requester_party", "requester_party_id"}.intersection(update_fields or ()):
            raise ValidationError({"requester_party": "Change requester identity through the round instance."})
        return super().bulk_create(
            objs, batch_size, ignore_conflicts, update_conflicts, update_fields, unique_fields,
        )


class _RoundBaseQuerySet(_RoundRequesterWrites, AngeeUnscopedQuerySet):
    """Keep native unfiltered manager writes under the requester identity contract."""


class RoundQuerySet[T: Round](_RoundRequesterWrites, AngeeQuerySet[T]):
    """Round collection scopes shared by record projections."""

    def active_for_project(self, project: Project) -> Self:
        """Select the newest live round targeting this project, its source task or track."""
        targets = models.Q(project=project) | models.Q(proposals__track=project)
        if project.converted_from_id is not None:
            targets |= models.Q(task_id=project.converted_from_id)
        return (
            self.filter(targets, status__in=(RoundStatus.COLLECTING, RoundStatus.OPENED))
            .distinct()
            .order_by(
                "-created_at",
                "-pk",
            )
        )
