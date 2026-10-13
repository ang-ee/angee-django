"""Transport-neutral queryset scoping adapters for Django models."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from itertools import batched
from typing import Any, TypeVar, cast

from django.core.exceptions import EmptyResultSet
from django.db import connections, models
from rebac import check_new, to_subject_ref
from rebac.backends import backend
from rebac.field_backing import _proposed_forward_relationships
from rebac.resources import model_resource_type
from rebac.schema.introspection import relation_dependencies
from rebac.watch import KEY_CHUNK
from rebac.watch import by_key as native_by_key

_ModelT = TypeVar("_ModelT", bound=models.Model)
_QuerySetT = TypeVar("_QuerySetT", bound=models.QuerySet[Any])


@dataclass(frozen=True, slots=True)
class CandidateRelationships:
    """The relations a row would carry for one action, as rebac's candidate preflight reads them.

    This is the one adapter over rebac's field-backing owner (still private
    there): a caller asks about a row that is not, or not yet, the stored one (an
    unsaved task's assignee, a retained history snapshot) and checks a subject
    against the relations its fields and ledger would give it.
    """

    resource_type: str
    definition: Any
    required: frozenset[str]
    relationships: dict[str, Any]

    def allows(self, subject: Any, action: str) -> bool:
        """Whether ``subject`` may perform ``action`` on the candidate these relations describe."""

        return check_new(
            subject=to_subject_ref(subject), action=action, resource_type=self.resource_type,
            relationships=self.relationships,
        ).allowed


def candidate_relationships(instance: models.Model, action: str, *, using: str | None) -> CandidateRelationships | None:
    """Read the relations ``instance`` would carry for ``action``; ``None`` when its type has no definition."""

    resource_type = model_resource_type(type(instance))
    if resource_type is None:
        return None
    schema = backend().schema()
    definition = schema.get_definition(resource_type)
    if definition is None:
        return None
    required = frozenset(relation_dependencies(schema, resource_type, action))
    relationships = _proposed_forward_relationships(instance, definition, required_relations=required, using=using)
    return CandidateRelationships(resource_type, definition, required, relationships)


def query_batch_size(queryset: models.QuerySet[Any], *, width: int = 1) -> int:
    """Bound native key batches by this query's remaining database parameters."""

    limit = connections[queryset.db].features.max_query_params
    if limit is None:
        return KEY_CHUNK
    try:
        _, params = queryset.query.get_compiler(using=queryset.db).as_sql()
    except EmptyResultSet:
        params = ()
    size = min(KEY_CHUNK, (limit - len(params)) // width)
    if size < 1:
        raise ValueError("The query leaves no database parameters for its keys.")
    return size


def by_key(queryset: _QuerySetT, keys: Sequence[Any]) -> Iterator[_QuerySetT]:
    """Compose REBAC key filtering with the database's native parameter budget."""

    if not keys:
        yield from native_by_key(queryset, keys)
    for chunk in batched(keys, query_batch_size(queryset)):
        yield from native_by_key(queryset, chunk)


def lock_if_supported(
    queryset: _QuerySetT,
    *,
    of: tuple[str, ...] = ("self",),
    skip_locked: bool = False,
    no_key: bool = False,
) -> _QuerySetT:
    """Declare row-lock intent; Django owns write routing and backend support.

    ``"self"`` means the whole row: for a multi-table child it also names every
    parent link, since Django's ``of`` locks only the tables it names.
    """

    if "self" in of:
        of = (*of, *(path for path in _parent_link_paths(queryset.model._meta.concrete_model) if path not in of))
    return queryset.select_for_update(of=of, skip_locked=skip_locked, no_key=no_key)


def _parent_link_paths(model: type[models.Model], prefix: str = "") -> tuple[str, ...]:
    """Return ``of`` paths reaching each concrete multi-table ancestor of ``model``."""

    paths: list[str] = []
    for parent, link in model._meta.parents.items():
        if link is None:
            continue
        path = f"{prefix}{link.name}"
        paths.append(path)
        paths.extend(_parent_link_paths(parent, f"{path}__"))
    return tuple(paths)


def bind_actor(instance: models.Model, actor: Any | None) -> None:
    """Bind ``actor`` to ``instance`` when the model owns REBAC row policy."""

    if actor is None:
        return
    if _is_angee_model(type(instance)):
        cast(Any, instance).with_actor(actor)
        return
    with_actor = getattr(instance, "with_actor", None)
    if callable(with_actor):
        with_actor(actor)


def aggregate_scoped_queryset(queryset: models.QuerySet[_ModelT]) -> models.QuerySet[_ModelT]:
    """Return the aggregate-safe scoped queryset for a REBAC model."""

    if queryset.query.is_empty():
        return queryset
    if requires_angee_rebac_contract(queryset.model):
        return cast(models.QuerySet[_ModelT], cast(Any, queryset).scoped_for_aggregate())
    if _is_angee_model(queryset.model):
        return queryset
    scoped = getattr(queryset, "scoped_for_aggregate", None)
    if callable(scoped):
        return cast(models.QuerySet[_ModelT], scoped())
    return queryset


def read_scoped_queryset(
    model: type[_ModelT],
    actor: Any | None,
    *,
    action: str = "read",
) -> models.QuerySet[_ModelT]:
    """Return readable rows; an absent actor cannot read protected models.

    Models without row policy retain their default queryset. Protected models
    without an actor or a scoping manager return an empty queryset.
    """

    manager = model._default_manager
    if not model_resource_type(model):
        return manager.all()
    if actor is None:
        return model._base_manager.none()
    if _is_angee_model(model):
        return cast(models.QuerySet[_ModelT], cast(Any, manager).with_actor(actor).with_action(action))
    with_actor = getattr(manager, "with_actor", None)
    if not callable(with_actor):
        return model._base_manager.none()
    queryset = with_actor(actor)
    with_action = getattr(queryset, "with_action", None)
    return cast(models.QuerySet[_ModelT], with_action(action) if callable(with_action) else queryset)


def write_scoped_queryset(model: type[_ModelT]) -> models.QuerySet[_ModelT]:
    """Return a write-target queryset with REBAC row scope and unredacted fields."""

    manager = model._default_manager
    if _is_angee_model(model):
        if requires_angee_rebac_contract(model):
            return cast(models.QuerySet[_ModelT], cast(Any, manager).for_write())
        return manager.all()
    for_write = getattr(manager, "for_write", None)
    if callable(for_write):
        return cast(models.QuerySet[_ModelT], for_write())
    return manager.all()


def system_queryset(
    model: type[_ModelT],
    *,
    lock: tuple[str, ...] | None = None,
) -> models.QuerySet[_ModelT]:
    """Return the model's unscoped system queryset, with a third-party fallback."""

    owner = getattr(model, "system_queryset", None)
    if callable(owner):
        return cast(models.QuerySet[_ModelT], owner(lock=lock))
    queryset = model._base_manager.all()
    system_context = getattr(queryset, "system_context", None)
    if callable(system_context):
        queryset = system_context(reason=f"{model._meta.label_lower}.system_queryset")
    if lock is not None:
        queryset = lock_if_supported(queryset, of=lock)
    return cast(models.QuerySet[_ModelT], queryset)


def requires_angee_rebac_contract(model: type[models.Model]) -> bool:
    """Return whether ``model`` is an Angee model with declared row authorization."""

    return _is_angee_model(model) and bool(model_resource_type(model))


def _is_angee_model(model: type[models.Model]) -> bool:
    return callable(getattr(model, "system_queryset", None))
