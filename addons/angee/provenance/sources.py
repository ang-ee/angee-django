"""Statement-principal classification owned by provenance, outside generic capture."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ImproperlyConfigured, ValidationError
from rebac import to_subject_ref
from rebac.resources import model_for_subject_type


def source_kind_for(subject: Any) -> str | None:
    """Return a persisted statement principal's declared source kind."""

    source = to_subject_ref(subject)
    resolved = model_for_subject_type(source.subject_type)
    if resolved is None or source.optional_relation or source.subject_id == "*":
        return None
    model, id_attr = resolved
    if not getattr(model, "statement_source", False):
        return None
    try:
        principal = model._base_manager.get(**{id_attr: source.subject_id})
        kind = principal.source_kind
        return str(kind) if kind is not None else None
    except (model.DoesNotExist, TypeError, ValueError, ImproperlyConfigured, ValidationError):
        return None
