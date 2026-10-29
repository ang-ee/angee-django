"""Clock-aligned admission stays bounded across native cache backends."""

from pathlib import Path
from unittest.mock import Mock

import pytest
from django.core.cache.backends.base import BaseCache
from django.core.cache.backends.filebased import FileBasedCache
from django.core.cache.backends.locmem import LocMemCache

from angee.cache import increment_window


@pytest.mark.parametrize("backend", ["memory", "file"])
def test_counter_does_not_extend_the_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, backend: str,
) -> None:
    """A backend extending key TTL cannot extend the clock-aligned window."""

    now = [100.0]
    monkeypatch.setattr("time.time", lambda: now[0])
    cache = LocMemCache("counter-expiry", {}) if backend == "memory" else FileBasedCache(str(tmp_path), {})
    cache.clear()
    assert increment_window(cache, "bucket", timeout=60) == 1
    now[0] = 119.0
    assert increment_window(cache, "bucket", timeout=60) == 2
    now[0] = 121.0
    assert increment_window(cache, "bucket", timeout=60) == 1
    cache.clear()


@pytest.mark.parametrize("another_request_started_window", [False, True])
def test_counter_recovers_expiry_between_add_and_increment(another_request_started_window: bool) -> None:
    """At the expiry boundary each competing request still spends one token."""

    cache = Mock(spec=BaseCache)
    cache.add.side_effect = [False, not another_request_started_window]
    cache.incr.side_effect = [ValueError("expired"), 2]
    assert increment_window(cache, "bucket", timeout=60) == (2 if another_request_started_window else 1)


def test_counter_propagates_a_second_expiry() -> None:
    """Repeated cache failure is bounded and lets the admission owner fail closed."""

    cache = Mock(spec=BaseCache)
    cache.add.return_value = False
    cache.incr.side_effect = ValueError("expired")
    with pytest.raises(ValueError, match="expired"):
        increment_window(cache, "bucket", timeout=60)
    assert cache.incr.call_count == 2
