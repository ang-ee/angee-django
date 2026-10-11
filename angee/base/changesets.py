"""Write attribution without coupling the model foundation to its storage addon."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from django.apps import apps
from django.conf import settings
from django.db import models
from django.utils.functional import SimpleLazyObject
from rebac import app_settings, current_actor
from rebac.actors import current_sudo_reason
from rebac.errors import MissingActorError

_current_changeset: ContextVar[Any | None] = ContextVar("angee_current_changeset", default=None)


def current_changeset(*, required: bool = False, record: bool = False) -> Any | None:
    """Return the open changeset, optionally recording it before a captured write.

    Empty boundaries create no database rows, allowing checks and schema commands
    to run before migrations. Strictness follows REBAC's existing posture.
    """

    instance = _current_changeset.get()
    if instance is None:
        if required and app_settings.REBAC_STRICT_MODE:
            raise MissingActorError("A captured write requires an open changeset.")
        return None
    if record and instance.pk is None:
        instance.save(force_insert=True)
    return instance


@contextmanager
def changeset(
    *,
    action: str = "U",
    activity: str = "manual",
    change_reason: str | None = None,
    correlation_id: str = "",
    idempotency_key: str = "",
    origin: models.Model | None = None,
    occurred_at: datetime | None = None,
) -> Iterator[Any]:
    """Open one logical write boundary as REBAC's ambient actor.

    Nested boundaries join the outer changeset. This context never grants a
    bypass or changes the actor; authorized system boundaries own those choices.
    Model construction and persistence are deferred, so a command can enter
    before Django populates its apps. A boundary may mark a handled failure by
    setting ``outcome="failure"`` on the yielded changeset.
    """

    existing = _current_changeset.get()
    if existing is not None:
        yield existing
        return

    actor = current_actor()
    reason = change_reason if change_reason is not None else current_sudo_reason() or ""
    occurred_at = occurred_at or datetime.now(UTC)
    instance: Any | None = None

    def construct() -> Any:
        nonlocal instance
        model = apps.get_model(settings.ANGEE_CHANGESET_MODEL)
        instance = model(
            actor=str(actor) if actor is not None else "",
            action=action,
            activity=activity,
            change_reason=reason,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
        )
        if origin is not None:
            instance.origin = origin
        return instance

    deferred = SimpleLazyObject(construct)
    token = _current_changeset.set(deferred)
    outcome = "failure"
    try:
        yield deferred
        outcome = "success"
    finally:
        try:
            if instance is not None and instance.pk is not None:
                instance.state = "closed"
                instance.outcome = "failure" if instance.outcome == "failure" else outcome
                instance.touched = sorted(set(instance.touched))
                instance.save(update_fields=("state", "outcome", "counts", "touched"))
        finally:
            _current_changeset.reset(token)


@dataclass
class _WriteGroup:
    """Ephemeral deduplication state for one nested ORM dispatch."""

    seen: set[tuple[str, Any]] = field(default_factory=set)


_current_write_group: ContextVar[_WriteGroup | None] = ContextVar("angee_write_group", default=None)


@contextmanager
def write_group() -> Iterator[_WriteGroup]:
    """Share an identity during nested save/MTI/bulk dispatch, never across writes."""

    existing = _current_write_group.get()
    if existing is not None:
        yield existing
        return
    group = _WriteGroup()
    token = _current_write_group.set(group)
    try:
        yield group
    finally:
        _current_write_group.reset(token)
