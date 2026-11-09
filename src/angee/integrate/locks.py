"""Live task locks for integration bridge jobs."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from angee.base.locks import task_lock, task_lock_is_held


@contextmanager
def bridge_advisory_lock(bridge: Any) -> Iterator[bool]:
    """Try to hold ``Integration.sync_lock_key`` for ``bridge`` during a task."""

    if bridge.pk is None:
        yield False
        return
    with task_lock(bridge.sync_lock_key()) as acquired:
        yield acquired


def bridge_is_locked(bridge: Any) -> bool:
    """Return whether the live sync lock for ``bridge`` is currently held."""

    if bridge.pk is None:
        return False
    return task_lock_is_held(bridge.sync_lock_key())


def bridge_session_is_hosted(bridge: Any) -> bool:
    """Return whether a process host owns startup, execution, or child reaping."""

    return bridge.pk is not None and task_lock_is_held(bridge.live_session_host_lock_key())


__all__ = ["bridge_advisory_lock", "bridge_is_locked", "bridge_session_is_hosted"]
