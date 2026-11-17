"""GraphQL contributions for checklists: the record projection and the checklist filters.

Owners of a checklist type compose :class:`ChecklistNode` onto the type's node,
which reads the record's ``checklist`` through the one status rule, page-batched:
one annotation per listed row and one read of the page's latest results. The
filters reach every checklist type's resource through GraphQL's resource-filter
hook (:func:`resource_filters`), so no model names them. Statuses are the
stored lowercase values on every surface, filters included.
"""

from __future__ import annotations

from datetime import datetime
from functools import partial
from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.db import models
from rebac import current_actor
from strawberry_django.fields.field import StrawberryDjangoField

from angee.base.permissions import require_rebac_queryset
from angee.checklists.managers import (
    ChecklistAssessment,
    ChecklistStatuses,
    assessment,
    checklist_category_status,
    checklist_next_blocking,
    checklist_status,
)
from angee.checklists.models import ChecklistMixin
from angee.graphql.subscriptions import changes

ChecklistResult = apps.get_model("checklists", "ChecklistResult")

_STATUSES_ATTR = "_checklist_statuses"
_LATEST_ATTR = "_checklist_latest_results"


@strawberry.type(name="ChecklistEvidenceReference")
class ChecklistEvidenceType:
    """A readable record that produced a result, addressed publicly."""

    model: str
    id: str


@strawberry.type(name="ChecklistItemAssessment")
class ChecklistItemType:
    """One item's status and its latest readable result."""

    key: str
    label: str
    category: str | None
    blocking: bool
    status: str
    detail: str
    stale: bool
    recorded_at: datetime | None
    evidence: list[ChecklistEvidenceType]


@strawberry.type(name="ChecklistGroupAssessment")
class ChecklistGroupType:
    """One category's status over its items."""

    key: str
    label: str
    status: str
    detail: str
    item_keys: list[str]


@strawberry.type(name="ChecklistAssessment")
class ChecklistType:
    """A record's checklist as its reader sees it."""

    status: str
    detail: str
    complete: bool
    stale: bool
    recorded_at: datetime | None
    groups: list[ChecklistGroupType]
    items: list[ChecklistItemType]


def _project(assessed: ChecklistAssessment) -> ChecklistType:
    return ChecklistType(
        status=assessed.status,
        detail=assessed.detail,
        complete=assessed.complete,
        stale=assessed.stale,
        recorded_at=assessed.recorded_at,
        groups=[
            ChecklistGroupType(
                key=group.key,
                label=group.label,
                status=group.status,
                detail=group.detail,
                item_keys=list(group.item_keys),
            )
            for group in assessed.groups
        ],
        items=[
            ChecklistItemType(
                key=item.key,
                label=item.label,
                category=item.category,
                blocking=item.blocking,
                status=item.status,
                detail=item.detail,
                stale=item.stale,
                recorded_at=item.recorded_at,
                evidence=[
                    ChecklistEvidenceType(model=ref.model, id=ref.id)
                    for ref in (getattr(item.result, "evidence_references", ()) if item.result is not None else ())
                ],
            )
            for item in assessed.items
        ],
    )


def _node_model(info: strawberry.Info) -> type[models.Model]:
    field = cast(StrawberryDjangoField, info._field)
    origin = field.origin_django_type
    if origin is None:
        raise TypeError("ChecklistNode composes onto a strawberry_django type.")
    return origin.model


def _statuses_annotation(info: strawberry.Info) -> models.Expression:
    """Each listed row's statuses, for the reader."""

    return ChecklistStatuses(_node_model(info), actor=current_actor(), select="projection")


def _latest_prefetch(info: strawberry.Info) -> models.Prefetch:
    """The page's latest readable results per item, for the reader."""

    del info
    rows = require_rebac_queryset(ChecklistResult, actor=current_actor())
    return models.Prefetch("_checklist_latest", queryset=rows, to_attr=_LATEST_ATTR)


@strawberry.type
class ChecklistNode:
    """Project a model's checklist on its node type.

    Compose alongside the node base, e.g. ``class InspectionType(ChecklistNode, AngeeNode)``;
    the model composes :class:`~angee.checklists.models.ChecklistMixin`.
    """

    @strawberry_django.field(annotate={_STATUSES_ATTR: _statuses_annotation}, prefetch_related=[_latest_prefetch])
    def checklist(self) -> ChecklistType:
        """Return the row's checklist: its summary, categories and items, as the reader sees them."""

        row = cast(Any, self)
        statuses = getattr(row, _STATUSES_ATTR, None)
        latest = getattr(row, _LATEST_ATTR, None)
        if statuses is None or latest is None:
            # An unoptimized root (a mutation payload, a hand-resolved row): one read for this row.
            return _project(ChecklistResult.objects.assess(row))
        return _project(assessment(type(row).checklist(), statuses, latest))


def resource_filters(model: type[models.Model]) -> dict[str, Any]:
    """Give each checklist type's resource its status filters, with no model declaration.

    ``checklist_status`` filters on the record's summary and ``checklist_<category>``
    on each category; ``checklist_next_blocking`` names the first blocking item not
    yet passed, so records still blocked filter on it being set.
    """

    if not issubclass(model, ChecklistMixin) or not (checklist := model.checklist()).items:
        return {}
    filters: dict[str, Any] = {"checklist_status": checklist_status}
    filters.update(
        {
            f"checklist_{category.key}": partial(checklist_category_status, category=category.key)
            for category in checklist.categories
        }
    )
    if any(item.blocking for item in checklist.items):
        filters["checklist_next_blocking"] = checklist_next_blocking
    return filters


schemas: dict[str, Any] = {
    "console": {
        "types": [ChecklistType],
        "subscription": [changes(ChecklistResult, field="checklistResultChanged")],
    },
}
"""The projection types, present wherever checklists is installed so web documents can name them, and the
change feed through which a recorded result refreshes its record's reads."""
