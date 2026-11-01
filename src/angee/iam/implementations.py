"""Actor-visible implementation defaults for protected models."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.core.exceptions import FieldDoesNotExist
from django.db import models

from angee.base.models.identity import public_id_of
from angee.base.models.implementations import _relation_default_queryset
from angee.iam.permissions import require_rebac_queryset


def readable_impl_defaults(model: type[models.Model], defaults: Mapping[str, Any]) -> dict[str, Any]:
    """Project row defaults through native field policy and actor-visible identities."""
    result = dict(defaults)
    for name, value in tuple(result.items()):
        try:
            field = model._meta.get_field(name)
        except FieldDoesNotExist:
            continue
        targets = _relation_default_queryset(
            field, value, querysets=lambda related: require_rebac_queryset(related).scoped()
        )
        if targets is None:
            continue
        target = targets.first()
        if target is None:
            del result[name]
        else:
            result[name] = public_id_of(target)
    return result
