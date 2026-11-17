"""Tests for the scheduling addon's recurrence and named time zones.

Recurrence and zone names are pure values — no model, no database — so these import
the fields and value objects directly and touch no rows; the timezone assertions pin
the timezone/window contract (see :class:`~angee.scheduling.apps.SchedulingConfig`)
by overriding ``TIME_ZONE`` per case.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from time import perf_counter
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
from angee.scheduling.recurrence import MAX_OCCURRENCES, Recurrence
from angee.scheduling.zones import ZoneName, get_zone_info

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
        return _Venue(timezone="America/Barbados")


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
def test_the_occurrence_after_a_moment_is_strictly_after_it(
    rule: str, moment: datetime, expected: datetime | None
) -> None:
    assert Recurrence(rule).get_occurrence_after(_utc(2026, 7, 6, 9), moment) == expected


@override_settings(TIME_ZONE="UTC")
@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        pytest.param("FREQ=WEEKLY;COUNT=3", _utc(2026, 7, 20, 9), id="count"),
        pytest.param("FREQ=WEEKLY;UNTIL=20260716T000000Z", _utc(2026, 7, 13, 9), id="until"),
        pytest.param("FREQ=WEEKLY", None, id="open-ended"),
        pytest.param("", _utc(2026, 7, 6, 9), id="single"),
    ],
)
def test_the_last_occurrence_is_where_a_bounded_rule_ends(rule: str, expected: datetime | None) -> None:
    assert Recurrence(rule).get_last_occurrence(_utc(2026, 7, 6, 9)) == expected


@override_settings(TIME_ZONE="UTC")
def test_a_rule_set_with_one_open_ended_member_never_ends() -> None:
    """Boundedness is read from the parsed rules, so a bounded first line cannot hide an endless second."""

    assert Recurrence("FREQ=DAILY;COUNT=2\nFREQ=DAILY").get_last_occurrence(_utc(2026, 7, 6, 9)) is None


@override_settings(TIME_ZONE="UTC")
def test_recurrence_refuses_more_occurrences_than_its_limit_and_never_truncates() -> None:
    huge = Recurrence(f"FREQ=DAILY;COUNT={MAX_OCCURRENCES + 1}")

    with pytest.raises(ValidationError):
        huge.validate()
    with pytest.raises(ValidationError):
        huge.get_last_occurrence(_utc(2026, 7, 6, 9))
    with pytest.raises(ValidationError):
        Recurrence("FREQ=HOURLY").occurrences(_utc(2026, 1, 1), _utc(2026, 1, 1), _utc(2032, 1, 1))


def test_a_floating_until_past_the_last_utc_instant_ends_with_the_representable_range() -> None:
    forever = Recurrence("FREQ=YEARLY;UNTIL=99991231T235959")

    forever.validate()
    last = forever.get_last_occurrence(_utc(2026, 1, 5, 14), zone="America/New_York")

    assert last == datetime(9999, 1, 5, 14, tzinfo=UTC)


def test_an_expansion_past_the_representable_dates_is_refused_never_overflowed() -> None:
    end_of_time = datetime.max.replace(tzinfo=UTC)

    with pytest.raises(ValidationError) as refused:
        Recurrence("FREQ=DAILY").occurrences(_utc(2026, 1, 1), end_of_time, end_of_time, zone="Pacific/Kiritimati")

    assert refused.value.code == "out_of_range"


def test_a_rule_that_never_matches_a_day_is_refused_at_once() -> None:
    february_thirtieth = Recurrence("FREQ=DAILY;BYMONTH=2;BYMONTHDAY=30")

    began = perf_counter()
    with pytest.raises(ValidationError) as refused:
        february_thirtieth.validate(dtstart=_utc(2026, 1, 1, 9))

    assert refused.value.code == "never_occurs"
    assert perf_counter() - began < 1


@pytest.mark.parametrize(
    "rule",
    [
        pytest.param("FREQ=WEEKLY;INTERVAL=2;BYDAY=TU", id="every-other-tuesday"),
        pytest.param("FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=29", id="leap-day"),
    ],
)
def test_a_rule_that_occurs_within_thirty_years_of_its_start_is_valid(rule: str) -> None:
    Recurrence(rule).validate(dtstart=_utc(2026, 3, 2, 9))


def test_a_rule_whose_interval_skips_every_matching_day_never_occurs_and_is_probed_fast() -> None:
    # Every seventh day from a Monday is a Monday: the Tuesday filter never matches.
    began = perf_counter()

    assert Recurrence("FREQ=DAILY;INTERVAL=7;BYDAY=TU").never_occurs(_utc(2026, 3, 2, 9), zone="UTC")
    assert perf_counter() - began < 1


def test_a_rule_ending_before_its_first_match_never_occurs() -> None:
    assert Recurrence("FREQ=YEARLY;BYMONTH=12;UNTIL=20260601T000000Z").never_occurs(_utc(2026, 1, 1), zone="UTC")


@pytest.mark.parametrize(
    "rule",
    [
        pytest.param("FREQ=DAILY\nFREQ=WEEKLY", id="rule-set"),
        pytest.param("DTSTART:20260101T090000Z\nRRULE:FREQ=DAILY", id="embedded-dtstart"),
        pytest.param("RRULE:FREQ=DAILY", id="property-prefix"),
        pytest.param("FREQ=DAILY\nEXDATE:20260102T090000Z", id="exdate-line"),
    ],
)
def test_only_one_bare_rule_validates(rule: str) -> None:
    with pytest.raises(ValidationError) as refused:
        Recurrence(rule).validate()

    assert refused.value.code == "invalid"
    with pytest.raises(ValidationError):
        RecurrenceField().clean(rule, None)


def test_a_sparse_rule_far_from_its_start_is_refused_before_any_walk() -> None:
    # Every fourth February 29 costs dateutil a step a day: the span alone refuses it.
    leap_days = Recurrence("FREQ=DAILY;BYMONTH=2;BYMONTHDAY=29")

    began = perf_counter()
    with pytest.raises(ValidationError) as refused:
        leap_days.occurrences(_utc(2028, 2, 29, 9), _utc(2100, 1, 1), _utc(2100, 3, 1), zone="UTC")

    assert refused.value.code == "series_too_long"
    assert perf_counter() - began < 0.5


def test_a_never_occurs_probe_at_the_end_of_time_is_refused_not_overflowed() -> None:
    with pytest.raises(ValidationError) as refused:
        Recurrence("FREQ=DAILY;INTERVAL=7;BYDAY=TU").validate(
            dtstart=datetime(9999, 12, 31, 23, tzinfo=UTC), zone="Pacific/Kiritimati"
        )

    assert refused.value.code == "out_of_range"


def test_a_span_too_long_to_represent_reaches_past_every_date_and_stays_valid() -> None:
    once_in_three_centuries = Recurrence("FREQ=YEARLY;INTERVAL=300")
    start = _utc(2026, 1, 1, 9)

    once_in_three_centuries.validate(dtstart=start)
    assert once_in_three_centuries.occurrences(start, _utc(2026, 1, 1), _utc(2027, 1, 1), zone="UTC") == [start]


def test_a_bounded_series_read_across_its_last_occurrence_is_never_refused() -> None:
    capped = Recurrence("FREQ=DAILY;COUNT=10000")
    start = _utc(2026, 1, 1, 9)
    last = datetime(2053, 5, 18, 9, tzinfo=UTC)  # the 10,000th day, inclusive of the start

    window = capped.occurrences(start, datetime(2053, 5, 15, tzinfo=UTC), datetime(2053, 6, 1, tzinfo=UTC), zone="UTC")

    assert window[-1] == last and len(window) == 4
    assert capped.get_last_occurrence(start, zone="UTC") == last


def test_the_spring_gap_instant_of_an_hourly_rule_counts_once() -> None:
    hourly = Recurrence("FREQ=HOURLY")
    start = datetime(2026, 3, 8, 0, tzinfo=ZoneInfo("America/New_York"))

    moments = hourly.occurrences(start, start, start + (datetime(2026, 3, 8, 6) - datetime(2026, 3, 8)),
                                 zone="America/New_York")

    assert len(moments) == len(set(moments))


def test_an_open_daily_series_twenty_years_on_still_expands_and_one_past_the_cap_refuses() -> None:
    daily = Recurrence("FREQ=DAILY")
    start = _utc(2026, 1, 1, 9)

    assert daily.occurrences(start, _utc(2046, 1, 1), _utc(2046, 1, 2), zone="UTC") == [_utc(2046, 1, 1, 9)]
    with pytest.raises(ValidationError) as refused:
        daily.occurrences(start, _utc(2200, 1, 1), _utc(2200, 1, 2), zone="UTC")
    assert refused.value.code == "series_too_long"


@pytest.mark.parametrize(
    ("rule", "refused"),
    [
        pytest.param("FREQ=HOURLY", False, id="hourly"),
        pytest.param("FREQ=MINUTELY", True, id="minutely"),
        pytest.param("FREQ=SECONDLY", True, id="secondly"),
    ],
)
def test_a_frequency_floor_refuses_finer_rules(rule: str, refused: bool) -> None:
    field = RecurrenceField(finest_frequency="HOURLY")

    if refused:
        with pytest.raises(ValidationError):
            field.clean(rule, None)
    else:
        assert field.clean(rule, None) == rule
    # Without a floor every frequency stays valid.
    assert RecurrenceField().clean(rule, None) == rule


@override_settings(TIME_ZONE="UTC")
def test_the_occurrence_after_unrolls_in_the_named_zone() -> None:
    """Monthly on the 31st, in a zone: the next occurrence keeps the local clock and skips short months."""

    barbados = ZoneInfo("America/Barbados")
    start = datetime(2026, 1, 31, 14, tzinfo=barbados)
    following = Recurrence("FREQ=MONTHLY").get_occurrence_after(start, start, zone="America/Barbados")
    assert following == datetime(2026, 3, 31, 14, tzinfo=barbados)
    assert following is not None and following.tzinfo is UTC


@pytest.mark.parametrize("name", ["America/Barbados", "UTC", ""])
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
def test_get_zone_info_reads_a_blank_name_as_the_project_zone() -> None:
    assert get_zone_info("") == ZoneInfo("America/New_York")
    assert get_zone_info("Europe/Brussels") == ZoneInfo("Europe/Brussels")
    with pytest.raises(ValidationError):
        get_zone_info("Nowhere/Town")


def test_time_zone_field_crosses_graphql_as_a_string() -> None:
    """``auto`` projects the field as ``String`` and returns the stored name unchanged."""

    schema = strawberry.Schema(query=_VenueQuery)
    assert "timezone: String!" in str(schema)
    result = schema.execute_sync("{ venue { timezone } }")
    assert result.errors is None
    assert result.data == {"venue": {"timezone": "America/Barbados"}}
