"""Bridge sync dispatch, and the context nested sync code reads: its progress bridge and its deadline."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from angee.integrate.models import Bridge


class SyncDispatch(StrEnum):
    """A durable execution owner will report this sync's terminal result later."""

    DISPATCHED = "dispatched"


_progress_bridge: ContextVar[Bridge | None] = ContextVar("angee_progress_bridge", default=None)


@contextmanager
def progress_bridge_context(bridge: Bridge) -> Iterator[Bridge]:
    """Make ``bridge`` the receiver of progress reports from nested sync code."""

    token = _progress_bridge.set(bridge)
    try:
        yield bridge
    finally:
        _progress_bridge.reset(token)


def current_progress_bridge() -> Bridge | None:
    """Return the bridge whose sync is running here; report through its ``report_progress``."""

    return _progress_bridge.get()


_sync_deadline: ContextVar[float | None] = ContextVar("angee_sync_deadline", default=None)


@contextmanager
def sync_deadline_context(deadline: float | None) -> Iterator[float | None]:
    """Make ``deadline`` (a ``time.monotonic()`` instant) the one the sync running here must stop by.

    The bridge sync sets it once for its cycle; a partition drained on a pool
    worker inherits it through the copied context.
    """

    token = _sync_deadline.set(deadline)
    try:
        yield deadline
    finally:
        _sync_deadline.reset(token)


def sync_deadline() -> float | None:
    """Return the ``time.monotonic()`` instant the sync running here must stop by, or ``None`` outside one.

    An adapter that parses or fetches where the driver passes no deadline (an
    identity read, the absence sweep's enumeration) stops there.
    """

    return _sync_deadline.get()


__all__ = [
    "SyncDispatch",
    "progress_bridge_context",
    "current_progress_bridge",
    "sync_deadline_context",
    "sync_deadline",
]
