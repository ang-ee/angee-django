"""Small operations composed from Django's native cache API."""

from __future__ import annotations

import time

from django.core.cache.backends.base import BaseCache


def increment_window(cache: BaseCache, key: str, *, timeout: int) -> int:
    """Spend one token in a clock-aligned window of ``timeout`` seconds.

    The bucket key bounds admission even when a backend's native increment
    refreshes its cache TTL. Recover one eviction race; backend failures and a
    second eviction propagate to the admission owner. Concurrency guarantees
    follow the selected backend's native add/increment.
    """

    key = f"{key}:{int(time.time() // timeout)}"
    if cache.add(key, 1, timeout=timeout):
        return 1
    try:
        return int(cache.incr(key))
    except ValueError:
        if cache.add(key, 1, timeout=timeout):
            return 1
        return int(cache.incr(key))
