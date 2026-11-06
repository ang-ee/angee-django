"""Public identity field metadata."""
from __future__ import annotations

from django.core.exceptions import FieldDoesNotExist
from django.db import models

from angee.base.fields.identity import SqidField


def public_data_id_field(model: type[models.Model]) -> SqidField | None:
    """Return the sqid field that makes ``model`` safe for public data surfaces."""

    try:
        field = model._meta.get_field("sqid")
    except FieldDoesNotExist:
        return None
    return field if isinstance(field, SqidField) else None
