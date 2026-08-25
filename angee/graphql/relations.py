"""Guard-aware relation fields for Strawberry-Django schemas."""

from __future__ import annotations

from typing import Any

import strawberry_django
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from rebac import current_actor

_UNCACHED = object()


def actor_scoped_to_one(field_name: str) -> Any:
    """Return a nullable to-one field that redacts targets unreadable by the actor.

    The parent may be actor-scoped or sudo-loaded: a cached related object is used
    only when REBAC stamped it for the current actor; otherwise the stored FK value
    is re-gated through the target model's actor-scoped manager. Missing access
    returns ``None`` rather than raising. Strawberry-Django's native prefetch hint
    batches a selected relation once per parent list, while ``only`` keeps the
    parent projection to the FK id this resolver reads.
    """

    def resolve(root: models.Model) -> Any:
        field = root._meta.get_field(field_name)
        if not isinstance(field, (models.ForeignKey, models.OneToOneField)):
            raise ImproperlyConfigured(
                f"{root._meta.label}.{field_name} must be a forward to-one relation"
            )

        fk_id = field.value_from_object(root)
        if fk_id is None:
            return None

        actor = current_actor()
        if actor is None:
            return None

        cached = root._state.fields_cache.get(field.name, _UNCACHED)
        if cached is None:
            return None
        if cached is not _UNCACHED and getattr(cached, "_rebac_actor", None) == actor:
            return cached

        related_model = field.remote_field.model
        queryset = related_model._default_manager.all()
        with_actor = getattr(queryset, "with_actor", None)
        if not callable(with_actor):
            raise ImproperlyConfigured(
                f"{root._meta.label}.{field_name} targets {related_model._meta.label}, "
                "whose default manager is not actor-scoped"
            )
        target_field = field.target_field
        return with_actor(actor).filter(**{target_field.attname: fk_id}).first()

    return strawberry_django.field(
        resolver=resolve,
        field_name=field_name,
        only=[f"{field_name}_id"],
        prefetch_related=[field_name],
    )


def actor_scoped_to_many(field_name: str) -> Any:
    """Return a to-many field whose rows are scoped to the current actor.

    The parent may be actor-scoped through one relation while the selected
    to-many relation contains other protected rows. Resolve the relation through
    the target model's actor-scoped queryset instead of exposing the raw related
    manager.
    """

    def resolve(root: models.Model) -> Any:
        field = root._meta.get_field(field_name)
        if not (
            getattr(field, "many_to_many", False)
            or getattr(field, "one_to_many", False)
        ):
            raise ImproperlyConfigured(
                f"{root._meta.label}.{field_name} must be a forward or reverse to-many relation"
            )

        actor = current_actor()
        if actor is None:
            return []

        cached = getattr(root, "_prefetched_objects_cache", {}).get(field_name, _UNCACHED)
        if cached is not _UNCACHED and all(
            getattr(row, "_rebac_actor", None) == actor for row in cached
        ):
            # Return the materialized rows, not the prefetched QuerySet: strawberry
            # -django's list qs_hook clones any returned QuerySet (dropping its
            # _result_cache) and re-fetches it once per parent, so handing back the
            # cache object turns the batched prefetch into an N+1.
            return list(cached)

        related_queryset = getattr(root, field_name).all()
        with_actor = getattr(related_queryset, "with_actor", None)
        if callable(with_actor):
            return with_actor(actor)

        related_model = field.related_model
        raise ImproperlyConfigured(
            f"{root._meta.label}.{field_name} targets {related_model._meta.label}, "
            "whose related manager is not actor-scoped"
        )

    return strawberry_django.field(
        resolver=resolve,
        field_name=field_name,
        prefetch_related=[field_name],
    )
