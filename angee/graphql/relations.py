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
