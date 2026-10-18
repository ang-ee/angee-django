"""Write attribution without coupling the model foundation to its storage addon."""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from threading import RLock
from types import TracebackType
from typing import Any, Protocol, cast

from asgiref.sync import sync_to_async
from django.apps import apps
from django.conf import settings
from django.db import connections, models, router, transaction
from django.db.models.base import ModelState
from django.utils import timezone
from rebac import app_settings, current_actor
from rebac.actors import current_sudo_reason
from rebac.errors import MissingActorError

from angee.base.autoconfig import SETTINGS
from angee.base.serialization import canonical_json_sha256, json_safe

logger = logging.getLogger(__name__)

MANAGEMENT_ENTRYPOINT = "angee.compose.manage.main"
"""The framework-owned ``manage.py`` entrypoint that opens a command's boundary."""


class ChangesetRequired(MissingActorError):
    """A captured write has no logical attribution boundary."""


def changeset_model_label() -> str:
    """Return the swappable storage label; empty when attribution storage is disabled."""

    return cast(str, getattr(settings, "ANGEE_CHANGESET_MODEL", SETTINGS["ANGEE_CHANGESET_MODEL"]) or "")


def changeset_model() -> type[models.Model]:
    """Return the configured concrete changeset model."""

    return cast(type[models.Model], apps.get_model(changeset_model_label()))


class ChangesetLike(Protocol):
    """Storage contract supplied by ANGEE_CHANGESET_MODEL."""

    pk: object
    actor: str
    change_reason: str
    touched: list[str]
    counts: dict[str, int]
    created_row: models.Model | None
    _state: ModelState

    def save(self, *, force_insert: bool = False, using: str | None = None) -> None: ...

    def close(self, outcome: str) -> None: ...

    def record_created(self, row: models.Model) -> None: ...


@dataclass
class _ChangesetRow:
    """The stored changeset of one writing thread and actor, touched only by that thread."""

    instance: ChangesetLike
    attributed: bool = False
    persisted: bool = False
    pending_commit: Callable[[], None] | None = None


class _ChangesetBoundary:
    """Shared facts of one logical boundary, stored as one row per writing thread and actor.

    Django connections are per thread, so a row's insert must share the thread
    and transaction of the captured writes that reference it; ``copy_context().run``
    in a worker pool therefore records its own row under the boundary's facts.
    A row per actor keeps attribution exact when one boundary serves several
    actors, such as a per-event trigger loop. Every row closes with the boundary.
    """

    def __init__(self, facts: dict[str, object], *, defaults: bool) -> None:
        self.facts = facts
        self.explicit = set() if defaults else set(facts)
        self.rows: dict[tuple[threading.Thread, str], _ChangesetRow] = {}
        self.failed = False
        self.lock = RLock()

    def mark_failed(self) -> None:
        self.failed = True

    @property
    def recording(self) -> bool:
        return any(row.pending_commit is not None or row.persisted for row in self.rows.values())

    def refine(self, facts: dict[str, object]) -> None:
        with self.lock:
            for name, value in facts.items():
                previous = self.facts.get(name)
                if previous != value and (name in self.explicit or self.recording):
                    raise ValueError(f"Conflicting changeset {name}: {previous!r} and {value!r}.")
            self.facts.update(facts)
            self.explicit.update(facts)
            for row in self.rows.values():
                for name, value in facts.items():
                    setattr(row.instance, name, value)

    def row(self) -> _ChangesetRow:
        """Return the current thread's row for the ambient actor, constructing it lazily.

        The thread object, not its recyclable identifier, is the key: Django binds
        connections to the thread object as well.
        """

        actor = current_actor()
        key = (threading.current_thread(), str(actor) if actor is not None else "")
        with self.lock:
            row = self.rows.get(key)
            if row is None:
                model = changeset_model()
                facts = dict(self.facts)
                origin = facts.pop("origin", None)
                facts.setdefault("occurred_at", timezone.now())
                instance = cast(ChangesetLike, model(actor=key[1], **facts))
                if origin is not None:
                    setattr(instance, "origin", origin)
                row = self.rows[key] = _ChangesetRow(instance)
            return row

    def capture(self, *, record: bool) -> ChangesetLike:
        row = self.row()
        instance = row.instance
        if not row.attributed:
            instance.change_reason = cast(str, self.facts.get("change_reason", current_sudo_reason() or ""))
            row.attributed = True
        if record and not row.persisted:
            using = router.db_for_write(type(instance), instance=instance)
            connection = connections[using]
            # Django removes callbacks on savepoint/transaction rollback and
            # retains them on savepoint release. The queue owns that distinction.
            if row.pending_commit is None or not any(
                callback is row.pending_commit for _, callback, _ in connection.run_on_commit
            ):
                instance.pk = None
                instance._state.adding = True
                instance.save(force_insert=True, using=using)

                def mark_persisted() -> None:
                    row.persisted = True

                row.pending_commit = mark_persisted
                transaction.on_commit(mark_persisted, using=using)
        return instance

    def discard(self, instance: ChangesetLike) -> None:
        """Forget a claim row whose insert lost a uniqueness race."""

        with self.lock:
            for key, row in tuple(self.rows.items()):
                if row.instance is instance:
                    del self.rows[key]
                    return

    def close(self, outcome: str) -> None:
        with self.lock:
            rows = list(self.rows.values())
        for row in rows:
            try:
                self._close_row(row, outcome)
            except Exception:
                logger.exception("Could not close changeset %s", row.instance.pk)

    @staticmethod
    def _close_row(row: _ChangesetRow, outcome: str) -> None:
        instance = row.instance
        using = router.db_for_write(type(instance), instance=instance)

        def close_persisted() -> None:
            if not row.persisted:
                return
            with transaction.atomic(using=using):
                instance.close(outcome)

        if connections[using].in_atomic_block:
            transaction.on_commit(close_persisted, using=using, robust=True)
        else:
            close_persisted()


_current_changeset: ContextVar[_ChangesetBoundary | None] = ContextVar("angee_current_changeset", default=None)


def _required_message() -> str:
    message = "A captured write requires an open changeset."
    if sys.argv and Path(sys.argv[0]).name == "manage.py":
        message += (
            f" This management command runs without a boundary: manage.py must call {MANAGEMENT_ENTRYPOINT}();"
            " re-render it from the project template."
        )
    return message


_process_boundary: _ChangesetBoundary | None = None
"""The boundary a whole-process entrypoint publishes to every thread it starts.

A management command (``angee.compose.manage.main``) and a test (the ``angee.testing``
fixture) own the process while they run, so a thread or pool they start without
copying the context (``threading.Thread``, ``ThreadPoolExecutor``) writes under their
boundary, in its own row. Concurrent entrypoints (requests, connections, tasks) never
publish one: their threads carry the context explicitly (``copy_context().run``).
"""


def _open_boundary() -> _ChangesetBoundary | None:
    """The boundary this thread writes under: its context's, else the process's."""

    return _current_changeset.get() or _process_boundary


def current_changeset(*, required: bool = False, record: bool = False) -> ChangesetLike | None:
    """Return the concrete open changeset, recording it before a captured write.

    Empty boundaries create no database rows, allowing checks and schema commands
    to run before migrations. Strictness follows REBAC's existing posture. The
    row belongs to the current thread and ambient actor; its sudo reason resolves
    on the first captured write (``required`` or ``record``).
    """

    boundary = _open_boundary()
    if boundary is None:
        if required and app_settings.REBAC_STRICT_MODE:
            raise ChangesetRequired(_required_message())
        return None
    if not changeset_model_label():
        return None
    return boundary.capture(record=record) if required or record else boundary.row().instance


def mark_changeset_failed() -> None:
    """Record a handled failure on the open boundary; its changesets close as failed."""

    boundary = _open_boundary()
    if boundary is not None:
        boundary.mark_failed()


def discard_changeset_claim(instance: ChangesetLike) -> None:
    """Remove the current boundary row after a rolled-back claim insert."""

    boundary = _open_boundary()
    if boundary is not None:
        boundary.discard(instance)


def creation_fingerprint(values: Any) -> str:
    """Return the canonical fingerprint shared by every idempotent create."""

    def content(value: Any) -> Any:
        if isinstance(value, Enum):
            return content(value.value)
        if isinstance(value, models.Model):
            return {"model": value._meta.label_lower, "pk": json_safe(value.pk)}
        if isinstance(value, Mapping):
            return {key: content(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [content(item) for item in value]
        return json_safe(value)

    return canonical_json_sha256(content(values))


class _ChangesetScope:
    """One ``changeset()`` entry, usable with ``with`` and ``async with``.

    The asynchronous exit closes the boundary in Django's thread-sensitive worker
    so an async entrypoint never hops threads for the request itself.
    """

    def __init__(self, facts: dict[str, object], *, fresh: bool, process: bool = False) -> None:
        self.facts = facts
        self.fresh = fresh
        self.process = process
        self.previous_process: _ChangesetBoundary | None = None
        self.boundary: _ChangesetBoundary | None = None
        self.joined: _ChangesetBoundary | None = None
        self.token: Token[_ChangesetBoundary | None] | None = None

    def __enter__(self) -> Callable[[], None]:
        global _process_boundary
        existing = _open_boundary()
        if existing is not None and not self.fresh:
            existing.refine(self.facts)
            self.joined = existing
            return existing.mark_failed
        self.boundary = _ChangesetBoundary(self.facts, defaults=self.fresh)
        self.token = _current_changeset.set(self.boundary)
        if self.process:
            self.previous_process, _process_boundary = _process_boundary, self.boundary
        return self.boundary.mark_failed

    def _leave(self, exc_type: type[BaseException] | None) -> str | None:
        """Reset the context; return the outcome to close, or ``None`` for a joined boundary."""

        if self.joined is not None:
            if exc_type is not None:
                self.joined.mark_failed()
            return None
        assert self.boundary is not None and self.token is not None
        _current_changeset.reset(self.token)
        if self.process:
            global _process_boundary
            _process_boundary = self.previous_process
        return "failure" if exc_type is not None or self.boundary.failed else "success"

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        outcome = self._leave(exc_type)
        if outcome is not None and self.boundary is not None:
            self.boundary.close(outcome)

    async def __aenter__(self) -> Callable[[], None]:
        return self.__enter__()

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        outcome = self._leave(exc_type)
        if outcome is not None and self.boundary is not None and self.boundary.rows:
            await sync_to_async(self.boundary.close)(outcome)


def changeset(
    *,
    action: str | None = None,
    activity: str | None = None,
    change_reason: str | None = None,
    correlation_id: str | None = None,
    origin: models.Model | None = None,
    occurred_at: datetime | None = None,
    fresh: bool = False,
    process: bool = False,
) -> _ChangesetScope:
    """Open logical attribution, yielding a callback to mark a handled failure.

    Entrypoints use ``fresh=True`` to own a boundary; their facts are defaults.
    Ad-hoc nested boundaries join and refine defaults before recording, while
    conflicting explicit facts fail fast. No boundary changes REBAC's actor or
    grants a bypass. A changeset row is constructed when a caller needs the
    concrete instance and persisted in the first captured write's transaction;
    each writing thread and actor records its own row under the shared facts.
    Asynchronous entrypoints use ``async with``. ``process=True`` is for an
    entrypoint that owns the whole process (a management command, a test): threads
    it starts without copying the context write under this boundary too.
    """

    facts: dict[str, object] = {
        name: value
        for name, value in (
            ("action", action),
            ("activity", activity),
            ("change_reason", change_reason),
            ("correlation_id", correlation_id),
            ("origin", origin),
            ("occurred_at", occurred_at),
        )
        if value is not None
    }
    return _ChangesetScope(facts, fresh=fresh, process=process)


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
