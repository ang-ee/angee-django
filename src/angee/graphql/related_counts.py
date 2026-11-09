"""Count a record's related groups, each through the read that lists its rows.

A related group lists rows that link one record to others (its files, pages,
source conversations, the runs that worked on it). The addon owning that group
declares how to count its rows as a :class:`RelatedCount` and appends a provider
returning its declarations to ``ANGEE_GRAPHQL_RELATED_COUNTS`` from its
autoconfig. The ``related_counts`` root resolves the record under the request
actor and counts every requested group in one statement, so a record's badge
needs no rows.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import strawberry
from django.apps import AppConfig, apps
from django.core import checks
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.signals import setting_changed
from django.db import models
from django.db.models.functions import Least
from django.dispatch import receiver
from rebac.resources import model_resource_type

from angee.base.hooks import resolve_hooks
from angee.base.querysets import AngeeQuerySet
from angee.graphql.ids import PublicID, instance_for_id

HOOK_SETTING = "ANGEE_GRAPHQL_RELATED_COUNTS"


@dataclass(frozen=True)
class RelatedCount:
    """How one related group counts its rows on a record, through their owner.

    ``id`` is the related group's id and names it in ``related_counts``. ``rows``
    returns, for one readable record, the actor-scoped Angee queryset of the owner
    method the group's row list calls, so the count and the rows authorize and
    select alike. ``limit`` is the most rows the group lists (its root's page
    bound), so a bounded group never counts more rows than it shows. ``target``
    limits the group to records of one model label (a ``<model>#groups`` group);
    ``None`` applies it to every record.
    """

    id: str
    rows: Callable[[Any], models.QuerySet[Any]]
    target: str | None = None
    limit: int | None = None

    def applies_to(self, record: models.Model) -> bool:
        """Whether ``record`` is a record this group's rows can point at."""

        return self.target is None or isinstance(record, apps.get_model(self.target))


@lru_cache(maxsize=1)
def declared_related_counts() -> dict[str, RelatedCount]:
    """Resolve every contributed declaration by group id; each hook returns an iterable of them."""

    declared: dict[str, RelatedCount] = {}
    for hook in resolve_hooks(HOOK_SETTING):
        for declaration in hook():
            if not isinstance(declaration, RelatedCount):
                raise ImproperlyConfigured(f"{HOOK_SETTING}: {declaration!r} is not a RelatedCount.")
            if declaration.id in declared:
                raise ImproperlyConfigured(f"{HOOK_SETTING}: two declarations claim the group {declaration.id!r}.")
            declared[declaration.id] = declaration
    return declared


@receiver(setting_changed)
def _forget_declarations(*, setting: str, **_: Any) -> None:
    if setting == HOOK_SETTING:
        declared_related_counts.cache_clear()


def check_related_counts(
    app_configs: Sequence[AppConfig] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Resolve every declaration and its target model, so a collision fails ``manage.py check``."""

    del app_configs, kwargs
    try:
        declared = declared_related_counts()
    except ImproperlyConfigured as error:
        return [checks.Error(str(error), id="angee.graphql.E004")]
    errors: list[checks.CheckMessage] = []
    for declaration in declared.values():
        if declaration.target is None:
            continue
        try:
            apps.get_model(declaration.target)
        except (LookupError, ValueError) as error:
            errors.append(checks.Error(
                f"{HOOK_SETTING}: {declaration.id!r} targets {declaration.target!r}: {error}",
                id="angee.graphql.E004",
            ))
    return errors


def count_related(record: models.Model, declarations: Sequence[RelatedCount]) -> dict[str, int]:
    """Count each group's rows on ``record`` in one statement over the record's own row.

    Every count is :meth:`AngeeQuerySet.readable_count_subquery` of the group's
    read under REBAC's eager aggregate scope, the row authorization the aggregate
    roots use. Building a read may run statements before that one: a
    ``require_access`` check on the record, the ancestor walk of a run selection. A group that does not apply to
    the record counts zero.
    """

    counts = dict.fromkeys((declaration.id for declaration in declarations), 0)
    applicable = [declaration for declaration in declarations if declaration.applies_to(record)]
    if not applicable:
        return counts
    expressions = {f"group_{index}": _count(declaration, record) for index, declaration in enumerate(applicable)}
    # The record was just read under the actor; its row only hosts the subqueries.
    host = models.QuerySet[models.Model](model=type(record)).filter(pk=record.pk)
    row = host.values(**expressions).get()
    counts.update({declaration.id: int(row[f"group_{index}"]) for index, declaration in enumerate(applicable)})
    return counts


def _count(declaration: RelatedCount, record: models.Model) -> models.Expression | models.Subquery:
    rows = declaration.rows(record)
    if not isinstance(rows, AngeeQuerySet):
        raise ImproperlyConfigured(
            f"{HOOK_SETTING}: {declaration.id!r} rows must be an Angee REBAC queryset, not {type(rows).__name__}.",
        )
    count = rows.readable_count_subquery(aggregate=True)
    if declaration.limit is None:
        return count
    return Least(count, models.Value(declaration.limit), output_field=models.IntegerField())


@strawberry.type(name="RelatedCount")
class RelatedCountType:
    """One requested related group's readable row count on a record."""

    group: str
    count: int


@strawberry.type
class RelatedCountQuery:
    """Related-group counts shared by every record view."""

    @strawberry.field(name="related_counts")
    def related_counts(self, model: str, id: PublicID, groups: list[str]) -> list[RelatedCountType]:
        """Count each named group's rows on one readable record, through each group's own read.

        An unknown group or model is a validation refusal. A hidden, absent or
        malformed record, or a model without a REBAC type, has no counts.
        """

        declared = declared_related_counts()
        if unknown := sorted(set(groups) - declared.keys()):
            raise ValidationError({"groups": f"Unknown related groups: {', '.join(unknown)}."})
        try:
            record_model = apps.get_model(model)
        except (LookupError, ValueError) as error:
            raise ValidationError({"model": f"Unknown model {model!r}."}) from error
        if model_resource_type(record_model) is None:
            return []
        record = instance_for_id(record_model, id)
        if record is None:
            return []
        requested = [declared[name] for name in dict.fromkeys(groups)]
        counts = count_related(record, requested)
        return [RelatedCountType(group=declaration.id, count=counts[declaration.id]) for declaration in requested]
