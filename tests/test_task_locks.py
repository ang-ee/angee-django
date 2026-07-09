"""Tests for the framework task lock abstraction."""

from __future__ import annotations

from angee.tasks.locks import LocalLockBackend, _advisory_pair, record_lock_key, task_lock, task_lock_is_held


def test_record_lock_key_is_stable() -> None:
    """Record lock names are stable across processes and Python runs."""

    key = record_lock_key("messaging.Channel", 42, "sync")

    assert key.namespace == "record"
    assert key.parts == ("messaging.Channel", "42", "sync")
    assert key.name == "angee:record:messaging.Channel:42:sync"


def test_local_lock_backend_excludes_same_key() -> None:
    """The local backend models advisory lock exclusion for unit tests."""

    backend = LocalLockBackend()
    key = record_lock_key("messaging.Channel", 42, "sync")
    first = backend.try_acquire(key)
    second = backend.try_acquire(key)

    assert first is not None
    assert second is None
    first.release()
    assert backend.try_acquire(key) is not None


def test_postgres_advisory_pair_uses_positive_int4_values() -> None:
    """Advisory keys stay in the pg_locks-friendly non-negative int4 range."""

    pair = _advisory_pair("angee:record:messaging.Channel:42:sync")

    assert len(pair) == 2
    assert all(0 <= value < 2**31 for value in pair)


def test_task_lock_releases_after_context(settings) -> None:
    """The context helper releases an acquired lock when the task body exits."""

    settings.ANGEE_TASK_LOCK_BACKEND = "angee.tasks.locks.LocalLockBackend"
    key = record_lock_key("messaging.Channel", 42, "sync")

    with task_lock(key) as acquired:
        assert acquired is True
        with task_lock(key) as reacquired:
            assert reacquired is False

    with task_lock(key) as acquired_again:
        assert acquired_again is True


def test_task_lock_reports_held_state(settings) -> None:
    """Read-side lock checks use the same configured backend."""

    settings.ANGEE_TASK_LOCK_BACKEND = "angee.tasks.locks.LocalLockBackend"
    key = record_lock_key("messaging.Channel", 84, "sync")

    assert task_lock_is_held(key) is False
    with task_lock(key) as acquired:
        assert acquired is True
        assert task_lock_is_held(key) is True
    assert task_lock_is_held(key) is False
