"""Generic ingestion context markers shared by addon sync surfaces."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_SYNC_INGESTION_DEPTH: ContextVar[int] = ContextVar("angee_sync_ingestion_depth", default=0)


@contextmanager
def sync_ingestion_context() -> Iterator[None]:
    """Mark the current call stack as framework-owned sync ingestion."""

    token = _SYNC_INGESTION_DEPTH.set(_SYNC_INGESTION_DEPTH.get() + 1)
    try:
        yield
    finally:
        _SYNC_INGESTION_DEPTH.reset(token)


def sync_ingestion_active() -> bool:
    """Return whether the current call stack is inside sync ingestion."""

    return _SYNC_INGESTION_DEPTH.get() > 0
