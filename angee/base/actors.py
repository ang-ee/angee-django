"""REBAC actor projection helpers for model code."""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models
from rebac import SubjectRef, app_settings, current_actor, to_subject_ref
from rebac.backends import backend
from rebac.resources import model_resource_type


def user_subject_type() -> str:
    """Return the REBAC subject type of the installed user model."""

    return model_resource_type(get_user_model()) or app_settings.REBAC_USER_TYPE


def instance_actor(instance: models.Model) -> SubjectRef | None:
    """Return the instance's pinned actor, falling back to the ambient actor.

    Instance verbs and audit defaults share this resolution, including during
    elevated writes. Plain Django models have only the ambient actor.
    """

    actor_getter = getattr(instance, "actor", None)
    actor = actor_getter() if callable(actor_getter) else None
    return actor if actor is not None else current_actor()


def actor_user_id(actor: Any) -> Any | None:
    """Return ``actor``'s user primary key, or ``None`` when no user backs it.

    Model-backed REBAC subjects use the database primary key, so the canonical
    actor id is already the value required by user foreign-key columns.
    Non-user subjects return ``None``.
    """

    if actor is None or not actor.subject_id:
        return None
    if actor.subject_type != user_subject_type():
        return None
    pk = get_user_model()._meta.pk
    if pk is None:
        return None
    try:
        return pk.to_python(actor.subject_id)
    except (TypeError, ValueError, ValidationError):
        return None


def subject_reaches_user(subject: models.Model | SubjectRef, user_id: Any) -> bool:
    """Return whether ``subject`` resolves to, or contains, the user with ``user_id``.

    A subject reaches a user when it is that user's own reference, a wildcard,
    or a userset such as ``auth/group:7#member`` whose relation the user holds on
    the referenced object. A record invariant that keeps one user out of its
    holders composes this before a grant, an assignment, or an admission and
    raises :class:`angee.base.errors.RecordAccessSubjectRefused` on ``True``.
    ``user_id`` of ``None`` names nobody and is never reached.
    """

    if user_id is None:
        return False
    reference = to_subject_ref(subject)
    if reference.subject_id == "*":
        return True
    user = SubjectRef.of(user_subject_type(), str(user_id))
    if reference == user:
        return True
    if not reference.optional_relation:
        return False
    return backend().check_access(subject=user, action=reference.optional_relation, resource=reference.object).allowed
