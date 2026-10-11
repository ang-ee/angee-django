"""Write attribution without coupling the model foundation to its storage addon."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock
from typing import Protocol, cast

from django.apps import apps
from django.conf import settings
from django.db import connections, models, router, transaction
from django.db.models.base import ModelState
from django.utils import timezone
from rebac import app_settings, current_actor
from rebac.actors import current_sudo_reason
from rebac.errors import MissingActorError

from angee.base.autoconfig import SETTINGS

logger = logging.getLogger(__name__)


class ChangesetRequired(MissingActorError):
    """A captured write has no logical attribution boundary."""


class ChangesetLike(Protocol):
    """Storage contract supplied by ANGEE_CHANGESET_MODEL."""

    pk: object
    actor: str
    change_reason: str
    touched: list[str]
    counts: dict[str, int]
    _state: ModelState

    def save(self, *, force_insert: bool = False, using: str | None = None) -> None: ...

    def close(self, outcome: str) -> None: ...


class _ChangesetBoundary:
    """Private lazy construction and Django-owned transaction admission."""

    def __init__(self, facts: dict[str, object], *, defaults: bool) -> None:
        self.facts = facts
        self.explicit = set() if defaults else set(facts)
        self.instance: ChangesetLike | None = None
        self.attributed = False
        self.persisted = False
        self.pending_commit: Callable[[], None] | None = None
        self.mark_failed: Callable[[], None] = lambda: None
        self.lock = RLock()

    def refine(self, facts: dict[str, object]) -> None:
        with self.lock:
            for name, value in facts.items():
                previous = getattr(self.instance, name) if self.instance is not None else self.facts.get(name)
                if previous != value and (name in self.explicit or self.pending_commit is not None or self.persisted):
                    raise ValueError(f"Conflicting changeset {name}: {previous!r} and {value!r}.")
            self.facts.update(facts)
            self.explicit.update(facts)
            if self.instance is not None:
                for name, value in facts.items():
                    setattr(self.instance, name, value)

    def construct(self) -> ChangesetLike:
        with self.lock:
            if self.instance is None:
                model = apps.get_model(getattr(settings, "ANGEE_CHANGESET_MODEL", SETTINGS["ANGEE_CHANGESET_MODEL"]))
                facts = dict(self.facts)
                origin = facts.pop("origin", None)
                facts.setdefault("occurred_at", timezone.now())
                self.instance = cast(ChangesetLike, model(**facts))
                if origin is not None:
                    setattr(self.instance, "origin", origin)
            return self.instance

    def capture(self, *, record: bool) -> ChangesetLike:
        with self.lock:
            instance = self.construct()
            if not self.attributed:
                actor = current_actor()
                instance.actor = str(actor) if actor is not None else ""
                instance.change_reason = cast(str, self.facts.get("change_reason", current_sudo_reason() or ""))
                self.attributed = True
            if record and not self.persisted:
                using = router.db_for_write(type(instance), instance=instance)
                connection = connections[using]
                # Django removes callbacks on savepoint/transaction rollback and
                # retains them on savepoint release. The queue owns that distinction.
                if self.pending_commit is None or not any(
                    callback is self.pending_commit for _, callback, _ in connection.run_on_commit
                ):
                    instance.pk = None
                    instance._state.adding = True
                    instance.save(force_insert=True, using=using)

                    def mark_persisted() -> None:
                        with self.lock:
                            self.persisted = True

                    self.pending_commit = mark_persisted
                    transaction.on_commit(mark_persisted, using=using)
            return instance

    def close(self, outcome: str) -> None:
        if self.instance is None:
            return
        try:
            instance = self.instance
            using = router.db_for_write(type(instance), instance=instance)

            def close_persisted() -> None:
                if not self.persisted:
                    return
                with transaction.atomic(using=using):
                    instance.close(outcome)

            if connections[using].in_atomic_block:
                transaction.on_commit(close_persisted, using=using, robust=True)
            else:
                close_persisted()
        except Exception:
            logger.exception("Could not close changeset %s", self.instance.pk)


_current_changeset: ContextVar[_ChangesetBoundary | None] = ContextVar("angee_current_changeset", default=None)


def current_changeset(*, required: bool = False, record: bool = False) -> ChangesetLike | None:
    """Return the concrete open changeset, recording it before a captured write.

    Empty boundaries create no database rows, allowing checks and schema commands
    to run before migrations. Strictness follows REBAC's existing posture. Actor
    and sudo reason resolve on the first captured write (``required`` or ``record``).
    """

    boundary = _current_changeset.get()
    if boundary is None:
        if required and app_settings.REBAC_STRICT_MODE:
            raise ChangesetRequired("A captured write requires an open changeset.")
        return None
    if not getattr(settings, "ANGEE_CHANGESET_MODEL", SETTINGS["ANGEE_CHANGESET_MODEL"]):
        return None
    return boundary.capture(record=record) if required or record else boundary.construct()


@contextmanager
def changeset(
    *,
    action: str | None = None,
    activity: str | None = None,
    change_reason: str | None = None,
    correlation_id: str | None = None,
    idempotency_key: str | None = None,
    origin: models.Model | None = None,
    occurred_at: datetime | None = None,
    fresh: bool = False,
) -> Iterator[Callable[[], None]]:
    """Open logical attribution, yielding a callback to mark a handled failure.

    Entrypoints use ``fresh=True`` to own a boundary; their facts are defaults.
    Ad-hoc nested boundaries join and refine defaults before recording, while
    conflicting explicit facts fail fast. No boundary changes REBAC's actor or
    grants a bypass. Construction is deferred until a caller needs the concrete
    instance; persistence follows the first captured write's transaction.
    """

    facts: dict[str, object] = {
        name: value
        for name, value in (
            ("action", action),
            ("activity", activity),
            ("change_reason", change_reason),
            ("correlation_id", correlation_id),
            ("idempotency_key", idempotency_key),
            ("origin", origin),
            ("occurred_at", occurred_at),
        )
        if value is not None
    }
    existing = _current_changeset.get()
    if existing is not None and not fresh:
        existing.refine(facts)
        try:
            yield existing.mark_failed
        except BaseException:
            existing.mark_failed()
            raise
        return

    boundary = _ChangesetBoundary(facts, defaults=fresh)
    token = _current_changeset.set(boundary)
    outcome = "failure"
    failed = False

    def mark_failed() -> None:
        nonlocal failed
        failed = True

    boundary.mark_failed = mark_failed
    try:
        yield mark_failed
        outcome = "failure" if failed else "success"
    finally:
        _current_changeset.reset(token)
        boundary.close(outcome)


@dataclass
class _WriteGroup:
    """Pending batch snapshots consumed once by each row's matching native receiver."""

    seen: set[tuple[str, object, str]] = field(default_factory=set)


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
