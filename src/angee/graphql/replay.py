"""Bounded model-group change history for subscription registration."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections import deque
from dataclasses import dataclass, field
from threading import RLock
from typing import Any
from weakref import WeakKeyDictionary

from channels.layers import InMemoryChannelLayer
from channels_redis.core import RedisChannelLayer
from django.conf import settings

from angee.graphql.constants import CHANGE_REPLAY_MAXLEN, CHANGE_REPLAY_SECONDS
from angee.graphql.events import ChangePayload


@dataclass
class _MemoryLog:
    entries: deque[tuple[float, str, dict[str, Any]]] = field(default_factory=deque)
    cursor: int = 0


_memory_logs: WeakKeyDictionary[InMemoryChannelLayer, dict[str, _MemoryLog]] = WeakKeyDictionary()
_failed_appends: WeakKeyDictionary[Any, dict[str, float]] = WeakKeyDictionary()
_memory_lock = RLock()


@dataclass(frozen=True)
class Replay:
    """A registration snapshot and its opaque last-seen stream position."""

    changes: tuple[ChangePayload, ...]
    cursor: str
    truncated: bool
    incomplete: bool = False


_APPEND = """
local now = redis.call('TIME')
local cutoff = now[1] * 1000 + math.floor(now[2] / 1000) - tonumber(ARGV[1]) * 1000
local id = redis.call('XADD', KEYS[1], 'MAXLEN', tonumber(ARGV[2]), '*', 'payload', ARGV[3])
redis.call('XTRIM', KEYS[1], 'MINID', tostring(cutoff) .. '-0')
redis.call('SET', KEYS[2], id, 'EX', tonumber(ARGV[1]))
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
return id
"""

_READ = """
local now = redis.call('TIME')
local cutoff = now[1] * 1000 + math.floor(now[2] / 1000) - tonumber(ARGV[1]) * 1000
local rows = redis.call('XRANGE', KEYS[1], tostring(cutoff) .. '-0', '+')
local marker = redis.call('EXISTS', KEYS[3])
local stream = redis.call('EXISTS', KEYS[1])
return {rows, redis.call('EXISTS', KEYS[2]), marker == 1 and stream == 0}
"""


def _redis_key(layer: RedisChannelLayer, group: str) -> str:
    return f"{layer.prefix}:replay:{group}"


def _cursor(group: str, position: str) -> str:
    """Hide the retained position and group activity from subscribers."""

    return hmac.new(settings.SECRET_KEY.encode(), f"{group}:{position}".encode(), hashlib.sha256).hexdigest()


async def note_failed_append(layer: Any, group: str) -> None:
    """Mark a lost in-window write so later local registrations refresh the model."""

    with _memory_lock:
        _failed_appends.setdefault(layer, {})[group] = time.monotonic() + CHANGE_REPLAY_SECONDS
    if isinstance(layer, RedisChannelLayer):
        connection = layer.connection(layer.consistent_hash(group))
        await connection.set(f"{_redis_key(layer, group)}:failed", "1", ex=CHANGE_REPLAY_SECONDS)


def _locally_incomplete(layer: Any, group: str) -> bool:
    with _memory_lock:
        expiry = _failed_appends.get(layer, {}).get(group, 0)
        if expiry <= time.monotonic():
            _failed_appends.get(layer, {}).pop(group, None)
            return False
        return True


def _prune(log: _MemoryLog, now: float) -> None:
    while log.entries and log.entries[0][0] < now - CHANGE_REPLAY_SECONDS:
        log.entries.popleft()


async def append_change(layer: Any, group: str, payload: dict[str, Any]) -> str:
    """Append before fanout; the Redis variant uses one server round trip."""

    if isinstance(layer, RedisChannelLayer):
        connection = layer.connection(layer.consistent_hash(group))
        result = await connection.eval(
            _APPEND, 2, _redis_key(layer, group), f"{_redis_key(layer, group)}:last",
            CHANGE_REPLAY_SECONDS, CHANGE_REPLAY_MAXLEN,
            json.dumps(payload, separators=(",", ":")),
        )
        return result.decode()
    if isinstance(layer, InMemoryChannelLayer):
        with _memory_lock:
            log = _memory_logs.setdefault(layer, {}).setdefault(group, _MemoryLog())
            _prune(log, time.monotonic())
            if len(log.entries) == CHANGE_REPLAY_MAXLEN:
                log.entries.popleft()
            log.cursor += 1
            log.entries.append((time.monotonic(), str(log.cursor), payload.copy()))
            return str(log.cursor)
    raise TypeError(f"Unsupported change channel layer: {type(layer).__name__}")


async def read_changes(layer: Any, group: str) -> Replay:
    """Read a consistent replay prefix after group registration."""

    if isinstance(layer, RedisChannelLayer):
        connection = layer.connection(layer.consistent_hash(group))
        key = _redis_key(layer, group)
        rows, failed, evicted = await connection.eval(
            _READ, 3, key, f"{key}:failed", f"{key}:last", CHANGE_REPLAY_SECONDS,
        )
        return Replay(
            tuple(ChangePayload.from_mapping(json.loads(fields[1])) for _, fields in rows),
            _cursor(group, rows[-1][0].decode() if rows else "0-0"),
            len(rows) >= CHANGE_REPLAY_MAXLEN,
            bool(failed) or bool(evicted) or _locally_incomplete(layer, group),
        )
    if isinstance(layer, InMemoryChannelLayer):
        with _memory_lock:
            log = _memory_logs.setdefault(layer, {}).setdefault(group, _MemoryLog())
            _prune(log, time.monotonic())
            return Replay(
                tuple(ChangePayload.from_mapping(payload) for _, _, payload in log.entries),
                _cursor(group, str(log.cursor)), len(log.entries) >= CHANGE_REPLAY_MAXLEN,
                _locally_incomplete(layer, group),
            )
    raise TypeError(f"Unsupported change channel layer: {type(layer).__name__}")
