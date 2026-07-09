"""Live task locks for integration bridge jobs."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from angee.tasks.locks import LockKey, record_lock_key, task_lock, task_lock_is_held


def bridge_lock_key(bridge: Any) -> LockKey:
    """Return the shared task lock key for one bridge row."""

    if bridge.pk is None:
        return record_lock_key(str(bridge._meta.label_lower), 0, "sync")
    sync_lock_key = getattr(bridge, "sync_lock_key", None)
    if callable(sync_lock_key):
        return sync_lock_key()
    return record_lock_key(str(bridge._meta.label_lower), bridge.pk, "sync")


@contextmanager
def bridge_advisory_lock(bridge: Any) -> Iterator[bool]:
    """Try to hold the live sync lock for ``bridge`` during a task."""

    if bridge.pk is None:
        yield False
        return
    with task_lock(bridge_lock_key(bridge)) as acquired:
        yield acquired


def bridge_is_locked(bridge: Any) -> bool:
    """Return whether the live sync lock for ``bridge`` is currently held."""

    if bridge.pk is None:
        return False
    return task_lock_is_held(bridge_lock_key(bridge))


__all__ = ["bridge_advisory_lock", "bridge_is_locked", "bridge_lock_key"]
