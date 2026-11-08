"""Tests for the base advisory lock owner."""

from __future__ import annotations

import multiprocessing
import threading
import time
import uuid

import psycopg
import pytest
from django.db import connection
from psycopg import sql

from angee.base import locks
from angee.base.locks import LocalLockBackend, LockKey, _advisory_pair, record_lock_key, task_lock, task_lock_is_held
from tests.task_lock_process import hold_postgres_advisory_lock


def _postgres_process_params() -> dict[str, object]:
    """Return the serializable libpq fields a spawned test process needs."""

    configured = connection.settings_dict
    return {
        "dbname": configured["NAME"],
        "user": configured.get("USER") or None,
        "password": configured.get("PASSWORD") or None,
        "host": configured.get("HOST") or None,
        "port": configured.get("PORT") or None,
    }


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


def test_local_blocking_acquire_waits_for_another_threads_release() -> None:
    """A blocking acquire waits for the holder and then owns the key."""

    backend = LocalLockBackend()
    key = record_lock_key("integrate.Credential", 7, "refresh")
    holder = backend.try_acquire(key)
    assert holder is not None
    acquired = threading.Event()
    handles = []

    def wait_for_key() -> None:
        handles.append(backend.acquire(key))
        acquired.set()

    waiter = threading.Thread(target=wait_for_key)
    waiter.start()
    assert not acquired.wait(0.1)
    holder.release()
    assert acquired.wait(5)
    waiter.join(5)
    assert backend.is_held(key) is True
    handles[0].release()
    assert backend.is_held(key) is False


def test_local_blocking_acquire_refuses_the_holders_own_key() -> None:
    """Waiting on a key this thread holds could never end, so it fails fast."""

    backend = LocalLockBackend()
    key = record_lock_key("integrate.Credential", 8, "refresh")
    holder = backend.try_acquire(key)
    assert holder is not None
    with pytest.raises(RuntimeError, match="not reentrant"):
        backend.acquire(key)
    holder.release()


@pytest.mark.django_db
def test_task_lock_blocking_acquires_a_free_key() -> None:
    """``blocking=True`` yields an owned key and releases it on exit."""

    key = record_lock_key("integrate.Credential", 9, "refresh")
    with task_lock(key, blocking=True) as acquired:
        assert acquired is True
        assert task_lock_is_held(key) is True
    assert task_lock_is_held(key) is False


def test_postgres_advisory_pair_uses_positive_int4_values() -> None:
    """Advisory keys stay in the pg_locks-friendly non-negative int4 range."""

    pair = _advisory_pair("angee:record:messaging.Channel:42:sync")

    assert len(pair) == 2
    assert all(0 <= value < 2**31 for value in pair)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("key_kind", ["bridge", "account"])
def test_postgres_advisory_probe_tracks_pair_lock_across_processes_only(key_kind: str) -> None:
    """Same bridge/account pair locks contend cross-process; bigint keys remain distinct."""

    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL advisory lock behavior")
    backend = locks.PostgresAdvisoryLockBackend()
    nonce = uuid.uuid4().hex
    key = (
        record_lock_key("messaging.Channel", nonce, "sync")
        if key_kind == "bridge"
        else LockKey("fake_live-account", (nonce,))
    )
    advisory_key = _advisory_pair(key.name)
    params = _postgres_process_params()
    context = multiprocessing.get_context("spawn")

    for pair, expected_held in ((True, True), (False, False)):
        parent_pipe, child_pipe = context.Pipe()
        process = context.Process(
            target=hold_postgres_advisory_lock,
            args=(params, advisory_key, pair, child_pipe),
        )
        process.start()
        try:
            assert parent_pipe.poll(10), "spawned lock holder did not report readiness"
            assert parent_pipe.recv() == "held"
            assert backend.is_held(key) is expected_held
            handle = backend.try_acquire(key)
            if expected_held:
                assert handle is None
            else:
                assert handle is not None
                handle.release()
        finally:
            if process.is_alive():
                parent_pipe.send("release")
            process.join(timeout=10)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
            parent_pipe.close()
            child_pipe.close()
            assert process.exitcode == 0


@pytest.mark.django_db(transaction=True)
def test_postgres_advisory_probe_ignores_same_pair_in_other_database() -> None:
    """Advisory liveness is scoped to the current database OID."""

    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL advisory lock behavior")
    params = _postgres_process_params()
    database_name = f"angee_lock_test_{uuid.uuid4().hex}"
    marker = f"Angee task lock cross-database test {database_name}"
    admin_params = {**params, "dbname": "postgres"}
    other_params = {**params, "dbname": database_name}
    key = record_lock_key("messaging.Channel", uuid.uuid4().hex, "sync")
    advisory_key = _advisory_pair(key.name)

    with psycopg.connect(**admin_params, autocommit=True) as admin, admin.cursor() as cursor:
        try:
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
            cursor.execute(
                sql.SQL("COMMENT ON DATABASE {} IS {}").format(sql.Identifier(database_name), sql.Literal(marker))
            )
            with psycopg.connect(**other_params, autocommit=True) as other, other.cursor() as other_cursor:
                other_cursor.execute("SELECT pg_advisory_lock(%s, %s)", advisory_key)
                backend = locks.PostgresAdvisoryLockBackend()
                assert backend.is_held(key) is False
                handle = backend.try_acquire(key)
                assert handle is not None
                handle.release()
        finally:
            cursor.execute(
                "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = %s",
                (database_name,),
            )
            if cursor.fetchone() == (marker,):
                cursor.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database_name)))


@pytest.mark.django_db(transaction=True)
def test_postgres_blocking_acquire_waits_for_another_session() -> None:
    """A second consumer waits in ``pg_advisory_lock`` until the holder releases."""

    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL advisory lock behavior")
    backend = locks.PostgresAdvisoryLockBackend()
    key = record_lock_key("integrate.Credential", uuid.uuid4().hex, "refresh")
    context = multiprocessing.get_context("spawn")
    parent_pipe, child_pipe = context.Pipe()
    process = context.Process(
        target=hold_postgres_advisory_lock,
        args=(_postgres_process_params(), _advisory_pair(key.name), True, child_pipe),
    )
    process.start()
    try:
        assert parent_pipe.poll(10), "spawned lock holder did not report readiness"
        assert parent_pipe.recv() == "held"
        assert backend.try_acquire(key) is None
        release = threading.Timer(0.5, parent_pipe.send, args=("release",))
        started = time.monotonic()
        release.start()
        handle = backend.acquire(key)
        assert time.monotonic() - started >= 0.4
        assert backend.is_held(key) is True
        handle.release()
        assert backend.is_held(key) is False
    finally:
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        parent_pipe.close()
        child_pipe.close()
        assert process.exitcode == 0


@pytest.mark.django_db
def test_task_lock_releases_after_context() -> None:
    """The context helper releases an acquired lock when the task body exits."""

    key = record_lock_key("messaging.Channel", 42, "sync")

    with task_lock(key) as acquired:
        assert acquired is True
        with task_lock(key) as reacquired:
            assert reacquired is False

    with task_lock(key) as acquired_again:
        assert acquired_again is True


@pytest.mark.django_db
def test_task_lock_reports_held_state() -> None:
    """Read-side lock checks use the same database-selected backend."""

    key = record_lock_key("messaging.Channel", 84, "sync")

    assert task_lock_is_held(key) is False
    with task_lock(key) as acquired:
        assert acquired is True
        assert task_lock_is_held(key) is True
    assert task_lock_is_held(key) is False


def test_task_lock_backend_matches_the_native_database() -> None:
    backend = locks.get_lock_backend()
    expected = locks.PostgresAdvisoryLockBackend if connection.vendor == "postgresql" else LocalLockBackend
    assert isinstance(backend, expected)
    assert backend.cross_process is (connection.vendor == "postgresql")
