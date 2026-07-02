"""REBAC actor projection helpers for model code."""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from rebac import app_settings, system_context


def actor_user_id(actor: Any) -> Any | None:
    """Return ``actor``'s user primary key, or ``None`` when it is not a user.

    The one reading of "this REBAC actor, as a user foreign-key id" — the value
    that backs user-owned columns (``created_by`` / ``updated_by`` /
    ``trashed_by`` …). The REBAC subject id may be a public id such as ``sqid``;
    FK columns need the database primary key.
    """

    if actor is None or actor.subject_type != app_settings.REBAC_USER_TYPE or not actor.subject_id:
        return None
    user_model = get_user_model()
    pk = user_model._meta.pk
    pk_name = pk.name if pk is not None else "pk"
    subject_id_attr = str(getattr(user_model._meta, "rebac_id_attr", None) or app_settings.REBAC_USER_ID_ATTR)
    if subject_id_attr in {"pk", pk_name}:
        return actor.subject_id
    with system_context(reason="base.actor_user_id"):
        return (
            user_model._base_manager.filter(**{subject_id_attr: actor.subject_id})
            .values_list(pk_name, flat=True)
            .first()
        )
