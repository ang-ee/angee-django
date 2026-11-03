"""Context-local actors and lazy, transaction-aware logical write boundaries."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime
from types import TracebackType
from typing import TYPE_CHECKING, Any, Protocol, cast

from asgiref.sync import sync_to_async
from django.apps import apps
from django.conf import settings
from django.db import connections, models, router, transaction
from django.utils import timezone

if TYPE_CHECKING:
    from angee.provenance.engine.base import AbstractChangeset

logger = logging.getLogger(__name__)
_NO_ACTOR = object()
_actor: ContextVar[object] = ContextVar("provenance_actor", default=_NO_ACTOR)
_reason: ContextVar[str | None] = ContextVar("provenance_reason", default=None)


def current_actor() -> object | None:
    actor = _actor.get()
    if actor is not _NO_ACTOR:
        return actor
    process = globals().get("_process_boundary")
    return process.actor if process is not None else None


def current_reason() -> str:
    reason = _reason.get()
    if reason is not None:
        return reason
    process = globals().get("_process_boundary")
    return process.reason if process is not None else ""


def actor_reference(actor: object | None) -> str:
    """Encode attribution without treating an opaque reference as authority."""
    if actor is None:
        return ""
    if isinstance(actor, str):
        return actor
    if isinstance(actor, models.Model):
        if actor.pk is None:
            raise ValueError("An actor must have a primary key.")
        return f"{actor._meta.label_lower}:{actor.pk}"
    raise TypeError("Actors must be Django models, strings, or None.")


@contextmanager
def actor_context(actor: object | None, change_reason: str = "") -> Iterator[None]:
    actor_reference(actor)
    actor_token, reason_token = _actor.set(actor), _reason.set(change_reason)
    try:
        yield
    finally:
        _reason.reset(reason_token)
        _actor.reset(actor_token)


class ChangesetRequired(RuntimeError):
    """A captured write requires a logical boundary."""


class ChangesetLike(Protocol):
    pk: Any
    actor_ref: str

    @property
    def action(self) -> str: ...

    @property
    def activity(self) -> str: ...

    @property
    def state(self) -> str: ...

    @property
    def outcome(self) -> str | None: ...

    scope: str
    key: str
    fingerprint: str
    created_row: models.Model | None
    _provenance_fresh: bool
    change_reason: str
    touched: list[str]
    counts: dict[str, int]
    _state: Any

    def save(self, **kwargs: Any) -> None: ...
    def close(self, outcome: str) -> None: ...
    def history_user(self) -> models.Model | None: ...
    def record_created(self, row: models.Model) -> None: ...


def changeset_model_label() -> str:
    return str(getattr(settings, "ANGEE_CHANGESET_MODEL", "") or "")


def changeset_model() -> type[models.Model]:
    return apps.get_model(changeset_model_label())


@dataclass
class _Row:
    instance: Any
    alias: str
    committed: bool = False
    callback: Callable[[], None] | None = None


class _Boundary:
    def __init__(self, facts: dict[str, object], defaults: bool, using: str | None = None) -> None:
        self.using = using
        self.facts = facts.copy()
        self.fresh = defaults
        self.actor = current_actor()
        self.reason = current_reason()
        self.explicit = set() if defaults else set(facts)
        self.rows: dict[tuple[threading.Thread, str, str], _Row] = {}
        self.lock = threading.RLock()
        self.failed = False

    def mark_failed(self) -> None:
        self.failed = True

    def refine(self, facts: dict[str, object]) -> None:
        with self.lock:
            written = any(row.committed or row.callback is not None for row in self.rows.values())
            for name, value in facts.items():
                if value != self.facts.get(name) and (written or name in self.explicit):
                    raise ValueError(f"Conflicting changeset {name}.")
            self.facts.update(facts)
            self.explicit.update(facts)
            for row in self.rows.values():
                for name, value in facts.items():
                    setattr(row.instance, name, value)

    def row(self, using: str | None) -> _Row:
        model = cast("type[AbstractChangeset]", changeset_model())
        if self.using is not None and using is not None and self.using != using:
            raise ValueError("A bound changeset must use its selected database.")
        alias = self.using or using or router.db_for_write(model)
        actor = current_actor()
        reference, reason = model.ambient_attribution()
        key = threading.current_thread(), alias, reference
        with self.lock:
            if key not in self.rows:
                facts: dict[str, object] = {
                    "occurred_at": timezone.now(),
                    "change_reason": reason,
                    **self.facts,
                }
                instance = model(actor_ref=reference)
                instance._state.db = alias
                for name, value in facts.items():
                    setattr(instance, name, value)
                instance._provenance_fresh = self.fresh
                instance._provenance_actor = actor
                self.rows[key] = _Row(instance, alias)
            return self.rows[key]

    def capture(self, record: bool, using: str | None) -> Any:
        row = self.row(using)
        if record and not row.committed:
            if "change_reason" not in self.facts:
                row.instance.change_reason = cast("type[AbstractChangeset]", changeset_model()).ambient_attribution()[1]
            connection = connections[row.alias]
            alive = row.callback is not None and any(item[1] is row.callback for item in connection.run_on_commit)
            if not alive:
                row.instance.pk = None
                row.instance.touched = []
                row.instance.counts = {}
                row.instance._state.adding = True
                row.instance.save(force_insert=True, using=row.alias)

                def committed() -> None:
                    row.committed = True

                row.callback = committed
                transaction.on_commit(committed, using=row.alias)
        return row.instance

    def close(self, outcome: str) -> None:
        for row in tuple(self.rows.values()):
            if not row.committed and row.callback is None:
                continue

            def finish(row: _Row = row) -> None:
                if row.committed:
                    row.instance.close(outcome)

            transaction.on_commit(finish, using=row.alias, robust=True)


_boundary: ContextVar[_Boundary | None] = ContextVar("provenance_boundary", default=None)
_process_boundary: _Boundary | None = None


def _open_boundary() -> _Boundary | None:
    return _boundary.get() or _process_boundary


def current_changeset(
    *, required: bool = False, record: bool = False, using: str | None = None
) -> ChangesetLike | None:
    if not changeset_model_label():
        if required:
            raise ChangesetRequired("A captured write requires an open changeset.")
        return None
    boundary = _open_boundary()
    if boundary is None:
        if required and cast("type[AbstractChangeset]", changeset_model()).context_required():
            error = getattr(changeset_model(), "required_context_error", ChangesetRequired)
            raise error("A captured write requires an open changeset.")
        return None
    return cast(ChangesetLike, boundary.capture(record, using))


def require_changeset(*, record: bool = False, using: str | None = None) -> ChangesetLike:
    result = current_changeset(required=True, record=record, using=using)
    if result is None:
        raise ChangesetRequired("A captured write requires an open changeset.")
    return result


def mark_changeset_failed() -> None:
    boundary = _open_boundary()
    if boundary is not None:
        boundary.mark_failed()


def is_current_changeset(instance: object) -> bool:
    """Whether this exact instance is storage owned by the open logical boundary.

    This check never allocates another alias's row or grants write authority.
    """

    boundary = _open_boundary()
    return boundary is not None and any(row.instance is instance for row in boundary.rows.values())


def discard_changeset_claim(instance: object) -> None:
    boundary = _open_boundary()
    if boundary is not None:
        for key, row in tuple(boundary.rows.items()):
            if row.instance is instance:
                del boundary.rows[key]


_UNSET = object()


class _Scope:
    def __init__(
        self,
        facts: dict[str, object],
        actor: object,
        fresh: bool,
        process: bool,
        using: str | None = None,
    ) -> None:
        self.using = using
        self.facts, self.actor, self.fresh, self.process = facts, actor, fresh, process
        self.owner: _Boundary | None = None
        self.joined = False
        self.token: Token[_Boundary | None] | None = None
        self.actor_token: Token[object] | None = None
        self.previous_process: _Boundary | None = None

    def __enter__(self) -> Callable[[], None]:
        global _process_boundary
        existing = _open_boundary()
        if self.actor is not _UNSET:
            actor_reference(self.actor)
        if existing is not None and not self.fresh:
            if self.using is not None and self.using != existing.using:
                raise ValueError("Joined changeset must use its existing database; open a fresh boundary")
            existing.refine(self.facts)
            self.owner, self.joined = existing, True
        else:
            self.owner = _Boundary(self.facts, self.fresh, self.using)
            if self.actor is not _UNSET:
                self.owner.actor = self.actor
            self.token = _boundary.set(self.owner)
            if self.process:
                self.previous_process, _process_boundary = _process_boundary, self.owner
        if self.actor is not _UNSET:
            self.actor_token = _actor.set(self.actor)
        return self.owner.mark_failed

    def leave(self, error: type[BaseException] | None) -> tuple[_Boundary, str] | None:
        global _process_boundary
        assert self.owner is not None
        if error is not None:
            self.owner.mark_failed()
        if self.actor_token is not None:
            _actor.reset(self.actor_token)
        if self.joined:
            return None
        assert self.token is not None
        _boundary.reset(self.token)
        if self.process:
            _process_boundary = self.previous_process
        return self.owner, "failure" if self.owner.failed else "success"

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        result = self.leave(kind)
        if result:
            result[0].close(result[1])

    async def __aenter__(self) -> Callable[[], None]:
        return self.__enter__()

    async def __aexit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        result = self.leave(kind)
        if result and result[0].rows:
            await sync_to_async(result[0].close)(result[1])


def changeset(
    *,
    actor: object = _UNSET,
    action: str | None = None,
    activity: str | None = None,
    change_reason: str | None = None,
    correlation_id: str | None = None,
    origin: models.Model | None = None,
    occurred_at: datetime | None = None,
    fresh: bool = False,
    process: bool = False,
    using: str | None = None,
) -> _Scope:
    """Open a lazy boundary; ordinary nested scopes refine their owning boundary."""
    facts: dict[str, object] = {
        name: value
        for name, value in {
            "action": action,
            "activity": activity,
            "change_reason": change_reason,
            "correlation_id": correlation_id,
            "origin": origin,
            "occurred_at": occurred_at,
        }.items()
        if value is not None
    }
    return _Scope(facts, actor, fresh, process, using)


@dataclass
class _WriteGroup:
    seen: set[tuple[str, object, str]] = field(default_factory=set)
    saved: dict[str, bool] = field(default_factory=dict)


_group: ContextVar[_WriteGroup | None] = ContextVar("provenance_write_group", default=None)


@contextmanager
def write_group() -> Iterator[_WriteGroup]:
    existing = _group.get()
    if existing is not None:
        yield existing
    else:
        group = _WriteGroup()
        token = _group.set(group)
        try:
            yield group
        finally:
            _group.reset(token)
