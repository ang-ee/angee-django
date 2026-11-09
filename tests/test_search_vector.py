"""The shared full-text stamp keeps PostgreSQL's size and vendor boundary."""

from unittest.mock import Mock

import pytest
from django.db import connection

from angee.base.search import SEARCH_CONFIG, SEARCH_MAX_BYTES, stamp_search_vector


def test_search_stamp_leaves_sqlite_rows_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(connection, "vendor", "sqlite")
    rows = Mock(db="default")
    stamp_search_vector(rows, "searchable")
    rows.update.assert_not_called()


def test_search_stamp_bounds_utf8_and_uses_simple(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(connection, "vendor", "postgresql")
    rows = Mock(db="default")
    stamp_search_vector(rows, "\x00" + "é" * SEARCH_MAX_BYTES)
    vector = rows.update.call_args.kwargs["search"]
    assert vector.config.config.value == SEARCH_CONFIG
    bounded = vector.get_source_expressions()[0].value
    assert len(bounded.encode("utf-8")) <= SEARCH_MAX_BYTES
    assert "\x00" not in bounded
