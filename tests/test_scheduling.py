"""Tests for the scheduling addon.

Recurrence is a pure value type — no model, no database — so these import the
field and value object directly and touch no rows; the timezone assertions pin
the timezone/window contract (see :class:`~angee.scheduling.apps.SchedulingConfig`)
by overriding ``TIME_ZONE`` per case.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ValidationError
from django.db import migrations, models
from django.db.migrations.state import ModelState
from django.db.migrations.writer import MigrationWriter
from django.test import override_settings
from django.test.utils import isolate_apps

from angee.scheduling.fields import RecurrenceField
from angee.scheduling.recurrence import Recurrence


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    """Build an aware UTC datetime (the canonical stored/window shape)."""

    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _local(tz_name: str, year: int, month: int, day: int, hour: int = 0) -> datetime:
    """Build a wall-clock moment in ``tz_name`` expressed as aware UTC.

    An all-day event stores midnight-in-project-tz converted to UTC; this is that
    conversion, used to seed all-day fixtures without hand-computing offsets.
    """

    return datetime(year, month, day, hour, tzinfo=ZoneInfo(tz_name)).astimezone(UTC)


@override_settings(TIME_ZONE="UTC")
def test_weekly_over_thirty_days_yields_five_occurrences() -> None:
    """FREQ=WEEKLY from 2026-07-06 09:00Z over a 30-day window: five Mondays-of-start."""

    dtstart = _utc(2026, 7, 6, 9)
    occurrences = Recurrence("FREQ=WEEKLY").occurrences(
        dtstart,
        window_start=_utc(2026, 7, 6),
        window_end=_utc(2026, 8, 5),  # +30 days, half-open
    )
    assert occurrences == [
        _utc(2026, 7, 6, 9),
        _utc(2026, 7, 13, 9),
        _utc(2026, 7, 20, 9),
        _utc(2026, 7, 27, 9),
        _utc(2026, 8, 3, 9),
    ]


@override_settings(TIME_ZONE="UTC")
def test_window_start_inclusive_end_exclusive_on_aware_utc_bounds() -> None:
    """A window opening on one occurrence and closing on another keeps the first, drops the last."""

    dtstart = _utc(2026, 7, 6, 9)
    occurrences = Recurrence("FREQ=WEEKLY").occurrences(
        dtstart,
        window_start=_utc(2026, 7, 6, 9),  # exactly the first occurrence
        window_end=_utc(2026, 7, 27, 9),  # exactly the fourth occurrence
    )
    assert _utc(2026, 7, 6, 9) in occurrences  # start inclusive
    assert _utc(2026, 7, 27, 9) not in occurrences  # end exclusive
    assert occurrences == [_utc(2026, 7, 6, 9), _utc(2026, 7, 13, 9), _utc(2026, 7, 20, 9)]


@override_settings(TIME_ZONE="America/New_York")
def test_daily_all_day_steps_local_midnight_across_a_dst_shift() -> None:
    """FREQ=DAILY from a local-midnight start stays local midnight while UTC shifts at DST."""

    ny = ZoneInfo("America/New_York")
    # 2026-03-08 is the US spring-forward; the window straddles it.
    dtstart = _local("America/New_York", 2026, 3, 6)  # local midnight -> 05:00Z (EST)
    occurrences = Recurrence("FREQ=DAILY").occurrences(
        dtstart,
        window_start=_local("America/New_York", 2026, 3, 6),
        window_end=_local("America/New_York", 2026, 3, 11),
    )

    local_dates = [occ.astimezone(ny).date() for occ in occurrences]
    assert local_dates == [datetime(2026, 3, d).date() for d in (6, 7, 8, 9, 10)]
    # Every occurrence is local midnight — the "by calendar date" guarantee.
    assert all(occ.astimezone(ny).time() == time(0, 0) for occ in occurrences)
    # The stored UTC offset shifts across DST while the local date does not:
    # 03-08 midnight is still EST (05:00Z), 03-09 midnight is EDT (04:00Z).
    assert occurrences[0].hour == 5  # EST
    assert occurrences[-1].hour == 4  # EDT


@override_settings(TIME_ZONE="UTC")
def test_absent_rule_yields_dtstart() -> None:
    """An empty rule returns exactly [dtstart] when it falls in the window."""

    dtstart = _utc(2026, 7, 6, 9)
    occurrences = Recurrence("").occurrences(
        dtstart,
        window_start=_utc(2026, 7, 6),
        window_end=_utc(2026, 7, 7),
    )
    assert occurrences == [dtstart]


@override_settings(TIME_ZONE="UTC")
def test_by_part_excluding_dtstart_drops_the_stored_start() -> None:
    """A Tuesday dtstart with BYDAY=MO yields Mondays only — the start never renders.

    Pins dateutil's DTSTART-must-synchronize semantics (the documented v1 deviation).
    """

    dtstart = _utc(2026, 7, 7, 9)  # 2026-07-07 is a Tuesday
    occurrences = Recurrence("FREQ=WEEKLY;BYDAY=MO").occurrences(
        dtstart,
        window_start=_utc(2026, 7, 6),
        window_end=_utc(2026, 7, 27),
    )
    # dateutil emits Mondays (07-13, 07-20); the Tuesday dtstart is absent.
    assert occurrences == [_utc(2026, 7, 13, 9), _utc(2026, 7, 20, 9)]
    assert dtstart not in occurrences


@override_settings(TIME_ZONE="America/New_York")
def test_naive_window_bound_raises_validation_error() -> None:
    """A naive window_start/window_end raises rather than guessing an offset."""

    aware = _utc(2026, 7, 6, 9)
    naive = datetime(2026, 7, 6, 9)  # no tzinfo
    with pytest.raises(ValidationError):
        Recurrence("FREQ=WEEKLY").occurrences(aware, window_start=naive, window_end=aware)
    with pytest.raises(ValidationError):
        Recurrence("FREQ=WEEKLY").occurrences(aware, window_start=aware, window_end=naive)


@override_settings(TIME_ZONE="America/New_York")
def test_naive_dtstart_raises_validation_error() -> None:
    """A naive dtstart is rejected too — every bound must be aware."""

    with pytest.raises(ValidationError):
        Recurrence("FREQ=WEEKLY").occurrences(
            datetime(2026, 7, 6, 9),
            window_start=_utc(2026, 7, 6),
            window_end=_utc(2026, 8, 5),
        )


def test_malformed_rule_raises_validation_error() -> None:
    """A rule dateutil cannot parse raises ValidationError, both directly and via the field."""

    with pytest.raises(ValidationError):
        Recurrence("FREQ=NONSENSE").validate()
    with pytest.raises(ValidationError):
        Recurrence("this is not a rule").validate()
    with pytest.raises(ValidationError):
        RecurrenceField().clean("also not a rule", None)


def test_valid_and_blank_rules_pass() -> None:
    """A well-formed RRULE and a blank value both validate without raising."""

    Recurrence("FREQ=WEEKLY;BYDAY=MO").validate()  # no raise
    Recurrence("").validate()  # blank is non-recurring, valid
    assert RecurrenceField().clean("FREQ=DAILY;COUNT=3", None) == "FREQ=DAILY;COUNT=3"
    assert RecurrenceField().clean("", None) == ""


def test_model_composing_the_field_migrates_cleanly() -> None:
    """A CreateModel over the field renders through MigrationWriter (exercises deconstruct)."""

    with isolate_apps("angee.scheduling"):

        class Event(models.Model):
            recurrence = RecurrenceField()

            class Meta:
                app_label = "scheduling"

        state = ModelState.from_model(Event)

    migration = migrations.Migration("0001_initial", "scheduling")
    migration.operations = [migrations.CreateModel(state.name, list(state.fields.items()))]

    rendered = MigrationWriter(migration).as_string()
    assert "angee.scheduling.fields.RecurrenceField" in rendered
