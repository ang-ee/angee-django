"""Tests for the sequence addon — allocation protocol, formatting, gaplessness.

The concurrency proof is PostgreSQL-marked: the gapless guarantee rests on
``SELECT … FOR UPDATE`` and is not claimed on the SQLite dev floor (see the
``angee.sequence.models`` module docstring).
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from django.db import connection, connections, transaction
from rebac import system_context

from angee.sequence.models import Sequence as AbstractSequence
from angee.sequence.models import SequenceCounter as AbstractSequenceCounter
from tests.conftest import _clear_model_tables, _create_missing_tables


class Sequence(AbstractSequence):
    """Concrete named counter used by sequence tests."""

    class Meta(AbstractSequence.Meta):
        """Django model options for the canonical test sequence."""

        abstract = False
        app_label = "sequence"
        db_table = "test_sequence_sequence"
        rebac_resource_type = "sequence/sequence"
        rebac_id_attr = "sqid"


class SequenceCounter(AbstractSequenceCounter):
    """Concrete per-period counter row used by sequence tests."""

    class Meta(AbstractSequenceCounter.Meta):
        """Django model options for the canonical test sequence counter."""

        abstract = False
        app_label = "sequence"
        db_table = "test_sequence_counter"


SEQUENCE_TEST_MODELS = (Sequence, SequenceCounter)
"""Concrete sequence models created on demand by sequence test fixtures."""


@pytest.fixture()
def sequence_tables(transactional_db: Any) -> Iterator[None]:
    """Create the concrete sequence tables for the duration of one test."""

    del transactional_db
    created_models = _create_missing_tables(SEQUENCE_TEST_MODELS)
    try:
        yield
    finally:
        _clear_model_tables(SEQUENCE_TEST_MODELS)
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


def _make_sequence(**fields: Any) -> Any:
    """Create one Sequence row under system_context (admin-only surface)."""

    with system_context(reason="sequence tests setup"):
        return Sequence.objects.create(**fields)


def _draw(key: str, **kwargs: Any) -> str:
    """Draw one number inside a transaction, as real callers do."""

    with transaction.atomic():
        return Sequence.objects.next_value(key, **kwargs)


def test_missing_key_fails_fast(sequence_tables: None) -> None:
    """An unknown key raises rather than inventing a sequence."""

    del sequence_tables
    with pytest.raises(Sequence.DoesNotExist):
        _draw("does.not.exist")


def test_template_formats_prefix_year_and_padded_number(sequence_tables: None) -> None:
    """The template renders {prefix}/{year}/{number} at storage precision."""

    del sequence_tables
    _make_sequence(
        key="invoice",
        name="Invoice",
        template="{prefix}INV/{year}/{number:05d}",
        prefix="AC-",
        period_reset="year",
    )
    assert _draw("invoice", on_date=date(2026, 7, 4)) == "AC-INV/2026/00001"


def test_no_reset_counts_monotonically_across_dates(sequence_tables: None) -> None:
    """A none-reset sequence ignores the date and never restarts."""

    del sequence_tables
    _make_sequence(key="entry", name="Entry", template="{number}", period_reset="none")
    assert _draw("entry", on_date=date(2026, 12, 31)) == "1"
    assert _draw("entry", on_date=date(2027, 1, 1)) == "2"
    assert _draw("entry", on_date=date(2027, 6, 1)) == "3"


def test_year_reset_restarts_at_the_year_boundary(sequence_tables: None) -> None:
    """A yearly reset partitions the counter by year."""

    del sequence_tables
    _make_sequence(key="so", name="Sales Order", template="{year}-{number:04d}", period_reset="year")
    assert _draw("so", on_date=date(2026, 6, 30)) == "2026-0001"
    assert _draw("so", on_date=date(2026, 12, 31)) == "2026-0002"
    assert _draw("so", on_date=date(2027, 1, 1)) == "2027-0001"


def test_month_reset_restarts_at_the_month_boundary(sequence_tables: None) -> None:
    """A monthly reset partitions the counter by year-month."""

    del sequence_tables
    _make_sequence(key="pay", name="Payment", template="{number:03d}", period_reset="month")
    assert _draw("pay", on_date=date(2026, 7, 15)) == "001"
    assert _draw("pay", on_date=date(2026, 7, 31)) == "002"
    assert _draw("pay", on_date=date(2026, 8, 1)) == "001"


def test_preview_is_none_when_disabled(sequence_tables: None) -> None:
    """preview_next declines to peek unless the sequence opts in."""

    del sequence_tables
    _make_sequence(key="q", name="Quote", template="{number}", preview_enabled=False)
    assert Sequence.objects.preview_next("q") is None


def test_preview_is_none_for_unknown_key(sequence_tables: None) -> None:
    """preview_next is advisory: an unknown key peeks nothing, never raises."""

    del sequence_tables
    assert Sequence.objects.preview_next("nope") is None


def test_preview_never_advances_the_counter(sequence_tables: None) -> None:
    """Repeated previews are stable and only a real draw advances the value."""

    del sequence_tables
    _make_sequence(
        key="draft",
        name="Draft",
        template="{number}",
        preview_enabled=True,
        period_reset="none",
    )
    assert Sequence.objects.preview_next("draft") == "1"
    assert Sequence.objects.preview_next("draft") == "1"
    assert _draw("draft") == "1"
    assert Sequence.objects.preview_next("draft") == "2"


@pytest.mark.skipif(
    os.environ.get("DATABASE_URL", "").split(":", 1)[0] not in {"postgres", "postgresql"},
    reason="gapless numbering is guaranteed only on PostgreSQL (row locks)",
)
def test_concurrent_draws_are_distinct_and_consecutive(sequence_tables: None) -> None:
    """N threads × M draws yield exactly the numbers 1…N×M, once each."""

    del sequence_tables
    if connection.vendor != "postgresql":
        pytest.skip("active Django connection is not PostgreSQL")

    _make_sequence(key="race", name="Race", template="{number}", period_reset="none")

    thread_count, draws_each = 4, 25
    drawn: list[int] = []
    guard = threading.Lock()

    def worker() -> None:
        local: list[int] = []
        try:
            for _ in range(draws_each):
                with transaction.atomic():
                    local.append(int(Sequence.objects.next_value("race")))
        finally:
            connections.close_all()
        with guard:
            drawn.extend(local)

    threads = [threading.Thread(target=worker) for _ in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    total = thread_count * draws_each
    assert len(drawn) == total
    assert sorted(drawn) == list(range(1, total + 1))
