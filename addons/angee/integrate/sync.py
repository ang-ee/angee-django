"""Bridge sync context markers owned by the integrate addon."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_BRIDGE_SYNC_DEPTH: ContextVar[int] = ContextVar("angee_integrate_bridge_sync_depth", default=0)


@contextmanager
def bridge_sync_context() -> Iterator[None]:
    """Mark the current call stack as bridge-owned sync ingestion."""

    token = _BRIDGE_SYNC_DEPTH.set(_BRIDGE_SYNC_DEPTH.get() + 1)
    try:
        yield
    finally:
        _BRIDGE_SYNC_DEPTH.reset(token)


def bridge_sync_active() -> bool:
    """Return whether the current call stack is inside bridge sync ingestion."""

    return _BRIDGE_SYNC_DEPTH.get() > 0
