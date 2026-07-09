"""Advisory task locks for queue workers."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import DEFAULT_DB_ALIAS, connections
from django.utils.module_loading import import_string


@dataclass(frozen=True)
class LockKey:
    """Stable advisory lock key."""

    namespace: str
    parts: tuple[str, ...]

    @property
    def name(self) -> str:
        """Return the canonical string key used by lock backends."""

        return "angee:" + ":".join((self.namespace, *self.parts))


class LockHandle(Protocol):
    """Owned advisory lock handle."""

    def release(self) -> None:
        """Release the lock if still owned."""


class LockBackend(Protocol):
    """Backend capable of acquiring one advisory task lock."""

    def try_acquire(self, key: LockKey, *, timeout: timedelta | None = None) -> LockHandle | None:
        """Return a handle when ``key`` is acquired, else ``None``."""

    def is_held(self, key: LockKey) -> bool:
        """Return whether ``key`` is currently held."""


@dataclass
class _LocalLockHandle:
    backend: LocalLockBackend
    key: LockKey
    released: bool = False

    def release(self) -> None:
        """Release a local lock once."""

        if self.released:
            return
        self.released = True
        self.backend.release(self.key)


class LocalLockBackend:
    """In-process lock backend for tests and SQLite development."""

    def __init__(self) -> None:
        """Create an empty local lock table."""

        self._mutex = threading.Lock()
        self._held: set[LockKey] = set()

    def try_acquire(self, key: LockKey, *, timeout: timedelta | None = None) -> LockHandle | None:
        """Acquire ``key`` when no local caller already holds it."""

        del timeout
        with self._mutex:
            if key in self._held:
                return None
            self._held.add(key)
        return _LocalLockHandle(self, key)

    def release(self, key: LockKey) -> None:
        """Release ``key`` from the local table."""

        with self._mutex:
            self._held.discard(key)

    def is_held(self, key: LockKey) -> bool:
        """Return whether ``key`` is held locally."""

        with self._mutex:
            return key in self._held


@dataclass
class _PostgresAdvisoryLockHandle:
    alias: str
    key: tuple[int, int]
    released: bool = False

    def release(self) -> None:
        """Release the Postgres session-level advisory lock once."""

        if self.released:
            return
        self.released = True
        with connections[self.alias].cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s, %s)", self.key)


class PostgresAdvisoryLockBackend:
    """Postgres session-level advisory lock backend."""

    def __init__(self, *, alias: str = DEFAULT_DB_ALIAS) -> None:
        """Store the Django database alias used for advisory locks."""

        self.alias = alias

    def try_acquire(self, key: LockKey, *, timeout: timedelta | None = None) -> LockHandle | None:
        """Acquire ``key`` through ``pg_try_advisory_lock``."""

        del timeout
        connection = connections[self.alias]
        if connection.vendor != "postgresql":
            raise ImproperlyConfigured("PostgresAdvisoryLockBackend requires a PostgreSQL database connection.")
        advisory_key = _advisory_pair(key.name)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s, %s)", advisory_key)
            acquired = bool(cursor.fetchone()[0])
        if not acquired:
            return None
        return _PostgresAdvisoryLockHandle(self.alias, advisory_key)

    def is_held(self, key: LockKey) -> bool:
        """Return whether Postgres reports ``key`` as held."""

        connection = connections[self.alias]
        if connection.vendor != "postgresql":
            raise ImproperlyConfigured("PostgresAdvisoryLockBackend requires a PostgreSQL database connection.")
        advisory_key = _advisory_pair(key.name)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_locks
                    WHERE locktype = 'advisory'
                      AND classid = %s
                      AND objid = %s
                      AND granted
                )
                """,
                advisory_key,
            )
            return bool(cursor.fetchone()[0])


_LOCAL_BACKEND = LocalLockBackend()
_CONFIGURED_BACKENDS: dict[str, LockBackend] = {}


def record_lock_key(model_label: str, pk: object, purpose: str) -> LockKey:
    """Return a stable task lock key for one model record and purpose."""

    return LockKey("record", (str(model_label), str(pk), str(purpose)))


@contextmanager
def task_lock(key: LockKey, *, timeout: timedelta | None = None) -> Iterator[bool]:
    """Yield whether the configured backend acquired ``key`` for this task."""

    handle = get_lock_backend().try_acquire(key, timeout=timeout)
    if handle is None:
        yield False
        return
    try:
        yield True
    finally:
        handle.release()


def task_lock_is_held(key: LockKey) -> bool:
    """Return whether the configured backend reports ``key`` as held."""

    return get_lock_backend().is_held(key)


def get_lock_backend() -> LockBackend:
    """Return the configured lock backend."""

    backend_path = getattr(settings, "ANGEE_TASK_LOCK_BACKEND", "")
    if backend_path:
        backend = _CONFIGURED_BACKENDS.get(str(backend_path))
        if backend is None:
            backend_factory = import_string(str(backend_path))
            backend = backend_factory()
            _CONFIGURED_BACKENDS[str(backend_path)] = backend
        if not hasattr(backend, "try_acquire"):
            raise ImproperlyConfigured(f"{backend_path} must implement try_acquire().")
        return backend
    if connections[DEFAULT_DB_ALIAS].vendor == "postgresql":
        return PostgresAdvisoryLockBackend()
    return _LOCAL_BACKEND


def _advisory_pair(name: str) -> tuple[int, int]:
    """Return a stable positive int4 pair for Postgres advisory locks."""

    digest = hashlib.sha256(name.encode("utf-8")).digest()
    return (_positive_int4(digest[:4]), _positive_int4(digest[4:8]))


def _positive_int4(raw: bytes) -> int:
    """Interpret four digest bytes inside Postgres' non-negative int4 range."""

    return int.from_bytes(raw, byteorder="big", signed=False) & 0x7FFFFFFF
