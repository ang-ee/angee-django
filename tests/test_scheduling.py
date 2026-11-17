"""Tests for the scheduling addon's recurrence and named time zones.

Recurrence and zone names are pure values — no model, no database — so these import
the fields and value objects directly and touch no rows; the timezone assertions pin
the timezone/window contract (see :class:`~angee.scheduling.apps.SchedulingConfig`)
by overriding ``TIME_ZONE`` per case.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest
import strawberry
import strawberry_django
from django.core.exceptions import ValidationError
from django.db import migrations, models
from django.db.migrations.state import ModelState
from django.db.migrations.writer import MigrationWriter
from django.test import override_settings
from django.test.utils import isolate_apps
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from angee.scheduling.fields import RecurrenceField, TimeZoneField
from angee.scheduling.recurrence import Recurrence
from angee.scheduling.zones import ZoneName, zone_info

with isolate_apps("angee.scheduling"):

    class _Venue(models.Model):
        """A row composing the zone field, for the GraphQL projection probe."""

        timezone = TimeZoneField()

        class Meta:
            app_label = "scheduling"


@strawberry_django.type(_Venue)
class _VenueType:
    timezone: strawberry.auto


@strawberry.type
class _VenueQuery:
    @strawberry.field
    def venue(self) -> _VenueType:
        return _Venue(timezone="America/Puerto_Rico")


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


def test_model_composing_the_fields_migrates_cleanly() -> None:
    """A CreateModel over the fields renders through MigrationWriter (exercises deconstruct)."""

    with isolate_apps("angee.scheduling"):

        class Event(models.Model):
            recurrence = RecurrenceField()
            timezone = TimeZoneField()

            class Meta:
                app_label = "scheduling"

        state = ModelState.from_model(Event)

    migration = migrations.Migration("0001_initial", "scheduling")
    migration.operations = [migrations.CreateModel(state.name, list(state.fields.items()))]

    rendered = MigrationWriter(migration).as_string()
    assert "angee.scheduling.fields.RecurrenceField" in rendered
    assert "angee.scheduling.fields.TimeZoneField" in rendered


@override_settings(TIME_ZONE="America/New_York")
def test_utc_until_validates_and_bounds_aware_wall_clock_recurrence() -> None:
    rule = Recurrence("FREQ=DAILY;UNTIL=20260309T130000Z")
    rule.validate()
    assert rule.occurrences(
        _local("America/New_York", 2026, 3, 7, 9),
        _utc(2026, 3, 7), _utc(2026, 3, 11),
    ) == [_utc(2026, 3, 7, 14), _utc(2026, 3, 8, 13), _utc(2026, 3, 9, 13)]


@pytest.mark.parametrize("until", ["20261029", "20261029T000000"])
def test_legacy_floating_until_validates(until: str) -> None:
    Recurrence(f"FREQ=DAILY;UNTIL={until}").validate()


@pytest.mark.parametrize("until,last_day,hour", [
    ("20260309", 9, 0), ("20260309T090000", 9, 9),
    ("20260309T130000Z", 9, 9),
])
@override_settings(TIME_ZONE="America/New_York")
def test_legacy_until_retains_local_clock_across_dst(until, last_day, hour):
    recurrence = Recurrence(f"FREQ=DAILY;UNTIL={until}")
    recurrence.validate()
    result = recurrence.occurrences(
        _local("America/New_York", 2026, 3, 6, hour),
        _local("America/New_York", 2026, 3, 7),
        _local("America/New_York", 2026, 3, 11),
    )
    assert result == [_local("America/New_York", 2026, 3, day, hour) for day in range(7, last_day + 1)]


@override_settings(TIME_ZONE="UTC")
def test_named_zone_drives_expansion_instead_of_the_project_zone() -> None:
    """A venue-time 09:00 daily rule stays 09:00 in its zone across DST while the project runs in UTC."""

    ny = ZoneInfo("America/New_York")
    occurrences = Recurrence("FREQ=DAILY").occurrences(
        _local("America/New_York", 2026, 3, 6, 9),
        window_start=_local("America/New_York", 2026, 3, 6),
        window_end=_local("America/New_York", 2026, 3, 11),
        zone="America/New_York",
    )
    assert [occ.astimezone(ny).time() for occ in occurrences] == [time(9, 0)] * 5
    assert occurrences[0].hour == 14 and occurrences[-1].hour == 13  # EST, then EDT
    with pytest.raises(ValidationError):
        Recurrence("FREQ=DAILY").occurrences(occurrences[0], occurrences[0], occurrences[-1], zone="Nowhere/Town")


@override_settings(TIME_ZONE="UTC")
@pytest.mark.parametrize(
    ("rule", "moment", "expected"),
    [
        pytest.param("FREQ=WEEKLY", _utc(2026, 7, 6, 9), _utc(2026, 7, 13, 9), id="strictly-after-an-occurrence"),
        pytest.param("FREQ=WEEKLY", _utc(2026, 7, 8), _utc(2026, 7, 13, 9), id="between-occurrences"),
        pytest.param("FREQ=WEEKLY", _utc(2026, 7, 1), _utc(2026, 7, 6, 9), id="before-the-start"),
        pytest.param("FREQ=WEEKLY;UNTIL=20260713T090000Z", _utc(2026, 7, 13, 9), None, id="series-ended"),
        pytest.param("", _utc(2026, 7, 1), _utc(2026, 7, 6, 9), id="single-before"),
        pytest.param("", _utc(2026, 7, 6, 9), None, id="single-passed"),
    ],
)
def test_after_returns_the_first_occurrence_strictly_after_a_moment(
    rule: str, moment: datetime, expected: datetime | None
) -> None:
    assert Recurrence(rule).after(_utc(2026, 7, 6, 9), moment) == expected


@override_settings(TIME_ZONE="UTC")
def test_after_unrolls_in_the_named_zone() -> None:
    """Monthly on the 31st, in a zone: the next occurrence keeps the local clock and skips short months."""

    pr = ZoneInfo("America/Puerto_Rico")
    start = datetime(2026, 1, 31, 14, tzinfo=pr)
    following = Recurrence("FREQ=MONTHLY").after(start, start, zone="America/Puerto_Rico")
    assert following == datetime(2026, 3, 31, 14, tzinfo=pr)
    assert following is not None and following.tzinfo is UTC


@pytest.mark.parametrize("name", ["America/Puerto_Rico", "UTC", ""])
def test_time_zone_field_admits_listed_and_blank_names(name: str) -> None:
    assert TimeZoneField().clean(name, None) == name


@pytest.mark.parametrize("name", ["Mars/Olympus_Mons", "Factory", "america/puerto_rico", "+02:00"])
def test_time_zone_field_and_zone_name_refuse_an_unlisted_name(name: str) -> None:
    """The Django field and the Pydantic type share one admission set and one message."""

    with pytest.raises(ValidationError) as field_error:
        TimeZoneField().clean(name, None)
    with pytest.raises(PydanticValidationError) as value_error:
        TypeAdapter(ZoneName).validate_python(name)
    assert field_error.value.code == "invalid_time_zone"
    assert field_error.value.messages[0] in str(value_error.value)


@override_settings(TIME_ZONE="America/New_York")
def test_zone_info_reads_a_blank_name_as_the_project_zone() -> None:
    assert zone_info("") == ZoneInfo("America/New_York")
    assert zone_info("Europe/Brussels") == ZoneInfo("Europe/Brussels")
    with pytest.raises(ValidationError):
        zone_info("Nowhere/Town")


def test_time_zone_field_crosses_graphql_as_a_string() -> None:
    """``auto`` projects the field as ``String`` and returns the stored name unchanged."""

    schema = strawberry.Schema(query=_VenueQuery)
    assert "timezone: String!" in str(schema)
    result = schema.execute_sync("{ venue { timezone } }")
    assert result.errors is None
    assert result.data == {"venue": {"timezone": "America/Puerto_Rico"}}
