"""REBAC actor projection helpers for model code."""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from rebac import app_settings
from rebac.resources import model_resource_type


def actor_user_id(actor: Any) -> Any | None:
    """Return ``actor``'s user primary key, or ``None`` when no user backs it.

    Model-backed REBAC subjects use the database primary key, so the canonical
    actor id is already the value required by user foreign-key columns.
    Non-user subjects return ``None``.
    """

    if actor is None or not actor.subject_id:
        return None
    user_model = get_user_model()
    if actor.subject_type != (model_resource_type(user_model) or app_settings.REBAC_USER_TYPE):
        return None
    pk = user_model._meta.pk
    if pk is None:
        return None
    try:
        return pk.to_python(actor.subject_id)
    except (TypeError, ValueError, ValidationError):
        return None
