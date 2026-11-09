"""Bridge sync dispatch and the bridge that receives nested progress reports."""

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
def bridge_progress_context(bridge: Bridge) -> Iterator[Bridge]:
    """Make ``bridge`` the receiver of progress reports from nested sync code."""

    token = _progress_bridge.set(bridge)
    try:
        yield bridge
    finally:
        _progress_bridge.reset(token)


def current_progress_bridge() -> Bridge | None:
    """Return the bridge whose sync is running here; report through its ``report_progress``."""

    return _progress_bridge.get()


__all__ = [
    "SyncDispatch",
    "bridge_progress_context",
    "current_progress_bridge",
]
