"""Tests for the scheduling addon's recurrence and named time zones.

Recurrence and zone names are pure values — no model, no database — so these import
the fields and value objects directly and touch no rows; the timezone assertions pin
the timezone/window contract (see :class:`~angee.scheduling.apps.SchedulingConfig`)
by overriding ``TIME_ZONE`` per case.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from time import perf_counter
from zoneinfo import ZoneInfo

import dateutil.rrule
import pytest
import strawberry
import strawberry_django
from django.core.exceptions import ValidationError
from django.db import migrations, models
from django.db.migrations.state import ModelState
from django.db.migrations.writer import MigrationWriter
from django.test import override_settings
from django.test.utils import isolate_apps
from hypothesis import assume, example, given, settings
from hypothesis import strategies as st
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


def test_a_sparse_rule_far_from_its_start_reads_its_window_and_the_gap_after_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Every fourth February 29: read seventy years on, the walk starts the period before the window.
    leap_days = Recurrence("FREQ=DAILY;BYMONTH=2;BYMONTHDAY=29")
    leap_days.never_occurs()  # its day filters' one-off probe is cached, not a read's cost
    periods = _count_periods(monkeypatch)

    found = leap_days.occurrences(_utc(2028, 2, 29, 9), _utc(2096, 1, 1), _utc(2096, 3, 1), zone="UTC")

    assert found == [_utc(2096, 2, 29, 9)]
    # dateutil scans the window and the gap to the next leap day (2100 is none), never the 68 years before it.
    assert periods() <= (datetime(2104, 2, 29) - datetime(2095, 12, 31)).days + 1
    assert periods() < (datetime(2096, 1, 1) - datetime(2028, 2, 29)).days // 4


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


def test_an_open_daily_series_reads_any_window_and_saving_counts_from_its_start() -> None:
    daily = Recurrence("FREQ=DAILY")
    start = _utc(2026, 1, 1, 9)

    assert daily.occurrences(start, _utc(2046, 1, 1), _utc(2046, 1, 2), zone="UTC") == [_utc(2046, 1, 1, 9)]
    assert daily.occurrences(start, _utc(2200, 1, 1), _utc(2200, 1, 2), zone="UTC") == [_utc(2200, 1, 1, 9)]
    # The save-time cap still counts from DTSTART: a horizon past 10,000 days refuses.
    with pytest.raises(ValidationError) as refused:
        daily.validate(dtstart=start, zone="UTC", through=_utc(2200, 1, 1))
    assert refused.value.code == "series_too_long"
    assert "from its start" in refused.value.message
    # A read refuses only a window holding more, and says so.
    with pytest.raises(ValidationError) as crowded:
        daily.occurrences(start, _utc(2100, 1, 1), _utc(2140, 1, 1), zone="UTC")
    assert crowded.value.code == "series_too_long"
    assert crowded.value.message.startswith("This window holds more than")


def _count_periods(monkeypatch: pytest.MonkeyPatch) -> Callable[[], int]:
    """Count the rule periods dateutil scans, matched or not (one day set each): the cost a read pays."""

    scanned = 0
    iterinfo = getattr(dateutil.rrule, "_iterinfo")
    for name in ("ydayset", "mdayset", "wdayset", "ddayset"):

        def counted(self: object, *args: int, _scan: Callable[..., object] = getattr(iterinfo, name)) -> object:
            nonlocal scanned
            scanned += 1
            return _scan(self, *args)

        monkeypatch.setattr(iterinfo, name, counted)
    return lambda: scanned


@pytest.mark.parametrize(
    ("rule", "start"),
    [
        pytest.param("FREQ=YEARLY", datetime(1, 1, 1, 9, tzinfo=UTC), id="yearly-from-year-one"),
        pytest.param("FREQ=DAILY", datetime(1976, 3, 1, 9, tzinfo=UTC), id="daily-from-fifty-years-ago"),
    ],
)
def test_a_one_month_read_walks_the_window_not_the_series_history(
    monkeypatch: pytest.MonkeyPatch, rule: str, start: datetime
) -> None:
    Recurrence(rule).never_occurs()  # its day filters' one-off probe is cached, not a read's cost
    periods = _count_periods(monkeypatch)

    found = Recurrence(rule).occurrences(start, _utc(2026, 1, 1), _utc(2026, 2, 1), zone="UTC")

    assert found and all(_utc(2026, 1, 1) <= moment < _utc(2026, 2, 1) for moment in found)
    # The period before the window, the window's own, and the one that ends the walk: never the series' past.
    assert periods() <= 31 + 3


def test_a_bounded_series_end_is_read_back_from_its_until(monkeypatch: pytest.MonkeyPatch) -> None:
    daily = Recurrence("FREQ=DAILY;UNTIL=20991231T000000Z")
    daily.never_occurs()  # its day filters' one-off probe is cached, not a read's cost
    periods = _count_periods(monkeypatch)

    last = daily.get_last_occurrence(_utc(2026, 7, 6, 9), zone="UTC")

    assert last == _utc(2099, 12, 30, 9)
    # A few days before UNTIL, not the 27,000 from its start.
    assert periods() <= 8
    # A last occurrence further back than a few periods is found by reading back further.
    leap_days = Recurrence("FREQ=DAILY;BYMONTH=2;BYMONTHDAY=29;UNTIL=20990301T000000Z")
    assert leap_days.get_last_occurrence(_utc(2028, 2, 29, 9), zone="UTC") == _utc(2096, 2, 29, 9)


@pytest.mark.parametrize(
    ("rule", "start", "window", "following"),
    [
        pytest.param("FREQ=YEARLY", datetime(1, 3, 1, 9), datetime(1, 6, 1), datetime(2, 3, 1, 9), id="yearly"),
        pytest.param("FREQ=MONTHLY", datetime(1, 1, 15, 9), datetime(1, 1, 20), datetime(1, 2, 15, 9), id="monthly"),
        pytest.param(
            "FREQ=YEARLY;INTERVAL=3", datetime(2, 3, 1, 9), datetime(3, 6, 1), datetime(5, 3, 1, 9), id="third-year"
        ),
        pytest.param("FREQ=WEEKLY", datetime(1, 1, 3, 9), datetime(1, 1, 5), datetime(1, 1, 10, 9), id="weekly"),
        pytest.param("FREQ=DAILY", datetime(1, 1, 1, 9), datetime(1, 1, 1, 12), datetime(1, 1, 2, 9), id="daily"),
    ],
)
def test_a_series_in_its_first_years_is_read_without_a_range_error(
    rule: str, start: datetime, window: datetime, following: datetime
) -> None:
    recurrence = Recurrence(rule)
    start, window, following = (moment.replace(tzinfo=UTC) for moment in (start, window, following))

    recurrence.validate(dtstart=start, zone="UTC")
    assert recurrence.get_occurrence_after(start, window, zone="UTC") == following
    assert recurrence.occurrences(start, window, following + timedelta(seconds=1), zone="UTC") == [following]
    assert recurrence.occurrences_after(start, window, following, zone="UTC") == [following]


@pytest.mark.parametrize("zone", ["UTC", "America/New_York", "Asia/Tehran"])
@pytest.mark.parametrize(
    "rule",
    [
        "FREQ=YEARLY;BYWEEKNO=1",
        "FREQ=YEARLY;BYWEEKNO=53;BYDAY=MO",
        "FREQ=WEEKLY;WKST=SU;BYDAY=SA,SU",
        "FREQ=MONTHLY;BYDAY=-1FR",
        "FREQ=YEARLY;BYYEARDAY=-1",
        "FREQ=DAILY;UNTIL=00010101T000000",
    ],
)
@pytest.mark.parametrize("start", [datetime(1, 1, 2, 9, tzinfo=UTC), datetime(9999, 12, 29, 9, tzinfo=UTC)])
def test_no_range_edge_error_escapes_a_recurrence(rule: str, start: datetime, zone: str) -> None:
    """At either end of the dates, every question is answered or refused as a ValidationError."""

    window = (start - timedelta(days=1), start + timedelta(days=2))
    for ask in (
        lambda recurrence: recurrence.validate(dtstart=start, zone=zone),
        lambda recurrence: recurrence.occurrences(start, *window, zone=zone),
        lambda recurrence: recurrence.occurrences_after(start, *window, zone=zone),
        lambda recurrence: recurrence.get_occurrence_after(start, window[0], zone=zone),
        lambda recurrence: recurrence.get_last_occurrence(start, zone=zone),
    ):
        try:
            ask(Recurrence(rule))
        except ValidationError as refused:
            assert refused.code in {"out_of_range", "never_occurs", "series_too_long"}


def test_a_rule_dateutil_refuses_at_its_start_is_invalid_not_an_error() -> None:
    # From 11:00 every second hour is odd: BYHOUR=10 is never reached, though the grammar holds.
    rule = Recurrence("FREQ=HOURLY;INTERVAL=2;BYHOUR=10")
    rule.validate()

    with pytest.raises(ValidationError) as refused:
        rule.validate(dtstart=_utc(2026, 1, 5, 11), zone="UTC")

    assert refused.value.code == "invalid"


def test_a_window_holds_instants_so_a_fall_back_hour_keeps_its_two_passes_apart() -> None:
    daily = Recurrence("FREQ=DAILY")
    start = datetime(2026, 10, 31, 1, 30, tzinfo=NEW_YORK)
    # 01:15 on its second pass (EST) is after 01:30 on its first (EDT).
    second_pass = datetime(2026, 11, 1, 1, 15, fold=1, tzinfo=NEW_YORK)

    found = daily.occurrences(start, second_pass, _utc(2026, 11, 2, 12), zone="America/New_York")

    assert found == [datetime(2026, 11, 2, 1, 30, tzinfo=NEW_YORK).astimezone(UTC)]
    assert daily.get_occurrence_after(start, second_pass, zone="America/New_York") == found[0]


NEW_YORK = ZoneInfo("America/New_York")
_WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
_ZONES = (
    "America/New_York", "America/Santiago", "America/Havana", "Asia/Beirut", "America/Sao_Paulo", "Asia/Tehran", "UTC"
)
_ERAS = {
    "recent": (datetime(1990, 1, 1), datetime(2030, 12, 31)),
    "first years": (datetime(1, 1, 5), datetime(3, 12, 31)),
    "last years": (datetime(9985, 1, 1), datetime(9997, 12, 31)),
}
_FIRST, _LAST = datetime(1, 1, 5, tzinfo=UTC), datetime(9999, 12, 1, tzinfo=UTC)
_Case = tuple[str, str, datetime, datetime, datetime]
"""A property case: (rule, zone, dtstart, window_start, window_end)."""


def _stamp(moment: datetime) -> str:
    """An RFC 5545 date-time, its year padded to four digits."""

    return moment.replace(tzinfo=None).isoformat(timespec="seconds").replace("-", "").replace(":", "")


def _some(draw: st.DrawFn, values: tuple[object, ...], most: int = 2) -> str:
    chosen = draw(st.lists(st.sampled_from(values), min_size=1, max_size=most, unique=True))
    return ",".join(str(value) for value in chosen)


@st.composite
def _cases(draw: st.DrawFn) -> _Case:
    """Draw ``(rule, zone, dtstart, window_start, window_end)``: daily to yearly, every BY part, any era."""

    zone = draw(st.sampled_from(_ZONES))
    # Hypothesis aims some draws at the zone's DST changes, both passes of a fall-back hour included.
    local = draw(st.datetimes(*_ERAS[draw(st.sampled_from(("recent", "recent", "first years", "last years")))],
                              timezones=st.just(ZoneInfo(zone))))
    dtstart = local.astimezone(UTC)
    freq = draw(st.sampled_from(("DAILY", "WEEKLY", "MONTHLY", "YEARLY")))
    parts = [f"FREQ={freq}", f"INTERVAL={draw(st.integers(1, 4))}"]
    if draw(st.booleans()):
        parts.append(f"WKST={draw(st.sampled_from(_WEEKDAYS))}")
    shape = draw(st.sampled_from(("none", "byday", "bymonthday", "bymonthday+byday", "byweekno", "byyearday")))
    if shape == "byday":
        days = _some(draw, _WEEKDAYS, 3)
        if freq in ("MONTHLY", "YEARLY") and draw(st.booleans()):
            days = ",".join(f"{draw(st.sampled_from((1, 2, -1, -2)))}{day}" for day in days.split(","))
        parts.append(f"BYDAY={days}")
    elif shape.startswith("bymonthday"):
        parts.append(f"BYMONTHDAY={_some(draw, (1, 2, 15, 28, 29, 30, 31, -1, -2, -31))}")
        if shape.endswith("byday"):
            parts.append(f"BYDAY={_some(draw, _WEEKDAYS, 3)}")
    elif shape == "byweekno" and freq == "YEARLY":
        parts.append(f"BYWEEKNO={_some(draw, (1, 2, 20, 52, 53, -1))}")
        if draw(st.booleans()):
            parts.append(f"BYDAY={_some(draw, _WEEKDAYS)}")
    elif shape == "byyearday" and freq == "YEARLY":
        parts.append(f"BYYEARDAY={_some(draw, (1, 59, 60, 100, 365, 366, -1, -366))}")
    if draw(st.booleans()):
        parts.append(f"BYMONTH={_some(draw, tuple(range(1, 13)), 3)}")
    if any(part.startswith(("BYDAY", "BYMONTHDAY", "BYWEEKNO", "BYYEARDAY")) for part in parts) and draw(st.booleans()):
        parts.append(f"BYSETPOS={draw(st.sampled_from((1, 2, -1, -2)))}")
    for name, values in (("BYHOUR", range(24)), ("BYMINUTE", range(60)), ("BYSECOND", range(1, 60))):
        if draw(st.booleans()):
            parts.append(f"{name}={_some(draw, tuple(values))}")
    room = (_LAST - dtstart).days
    if draw(st.booleans()):
        until = dtstart + timedelta(days=draw(st.integers(0, min(20 * 366, room))))
        floating = draw(st.booleans())
        parts.append(f"UNTIL={_stamp(until.astimezone(ZoneInfo(zone)))}" if floating else f"UNTIL={_stamp(until)}Z")
    back = min(60, (dtstart - _FIRST).days)
    window_start = dtstart + timedelta(
        days=draw(st.integers(-back, min(12 * 366, room - 63))), hours=draw(st.integers(0, 23))
    )
    return ";".join(parts), zone, dtstart, window_start, window_start + timedelta(days=draw(st.integers(1, 62)))


def _fall_back(year: int, month: int, day: int, hour: int, zone: str, rule: str) -> _Case:
    """A case whose DTSTART is the second pass of a fall-back hour, read a year on across the next one."""

    dtstart = datetime(year, month, day, hour, 30, fold=1, tzinfo=ZoneInfo(zone)).astimezone(UTC)
    window_start = dtstart + timedelta(days=364, minutes=-45)
    return rule, zone, dtstart, window_start, window_start + timedelta(days=3)


@settings(max_examples=200, deadline=4000, database=None)
@given(case=_cases())
@example(case=_fall_back(2025, 11, 2, 1, "America/New_York", "FREQ=DAILY;BYHOUR=1;BYMINUTE=0,30,45"))
@example(case=_fall_back(2025, 4, 5, 23, "America/Santiago", "FREQ=WEEKLY;BYDAY=SA,SU"))
@example(case=_fall_back(2025, 11, 2, 0, "America/Havana", "FREQ=DAILY;INTERVAL=2"))
@example(case=_fall_back(2025, 10, 25, 23, "Asia/Beirut", "FREQ=MONTHLY;BYMONTHDAY=25,26,-1"))
@example(case=_fall_back(2019, 2, 16, 23, "America/Sao_Paulo", "FREQ=YEARLY;BYMONTH=2;BYDAY=3SA"))
@example(case=_fall_back(2021, 9, 21, 23, "Asia/Tehran", "FREQ=WEEKLY;WKST=SU;BYDAY=TU,WE;BYSECOND=17"))
def test_a_reanchored_read_equals_the_full_expansion_in_its_window(
    case: _Case,
) -> None:
    """R-R3: each read from the period before its window answers exactly as dateutil's walk from DTSTART."""

    rule, zone, dtstart, window_start, window_end = case
    recurrence = Recurrence(rule)
    try:
        recurrence.validate(dtstart=dtstart, zone=zone)
    except ValidationError:
        assume(False)
    expanded: list[datetime] = []
    following: datetime | None = None
    beyond = False
    for moment in recurrence._compile(dtstart.astimezone(ZoneInfo(zone))):
        try:
            instant = moment.astimezone(UTC)
        except OverflowError:
            beyond = following is None
            break
        if following is None and instant > window_start:
            following = instant
        if instant > window_end:
            break
        expanded.append(instant)

    assert recurrence.occurrences(dtstart, window_start, window_end, zone=zone) == list(
        dict.fromkeys(moment for moment in expanded if window_start <= moment < window_end)
    )
    assert recurrence.occurrences_after(dtstart, window_start, window_end, zone=zone, limit=MAX_OCCURRENCES) == list(
        dict.fromkeys(moment for moment in expanded if window_start < moment <= window_end)
    )
    if beyond:
        # The next occurrence lies past the last UTC instant: the read refuses it rather than overflow.
        with pytest.raises(ValidationError):
            recurrence.get_occurrence_after(dtstart, window_start, zone=zone)
    else:
        assert recurrence.get_occurrence_after(dtstart, window_start, zone=zone) == following


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


@override_settings(TIME_ZONE="UTC")
@pytest.mark.parametrize("rule", [
    "FREQ=DAILY;BYMONTH=2;BYMONTHDAY=30", "FREQ=MINUTELY;BYMONTH=4;BYMONTHDAY=31", "FREQ=YEARLY;BYYEARDAY=367",
])
def test_a_rule_whose_days_never_match_expands_to_nothing_without_scanning(rule: str) -> None:
    """dateutil checks an end only at a candidate; a never-matching rule is decided in one cycle instead."""

    start = _utc(2026, 1, 1)
    recurrence = Recurrence(rule)
    clock = perf_counter()
    assert recurrence.never_occurs()
    assert recurrence.get_occurrence_after(start, start) is None
    assert recurrence.occurrences(start, start, _utc(2036, 1, 1)) == []
    assert recurrence.occurrences_after(start, start, _utc(2036, 1, 1)) == []
    assert perf_counter() - clock < 1
    assert not Recurrence("FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=29").never_occurs()
    assert not Recurrence("FREQ=MONTHLY;BYDAY=5MO").never_occurs()


@override_settings(TIME_ZONE="UTC")
def test_occurrences_after_is_open_at_after_and_closed_at_until_and_pages() -> None:
    daily = Recurrence("FREQ=DAILY")
    start = _utc(2026, 7, 1, 9)
    days = [_utc(2026, 7, day, 9) for day in (2, 3, 4)]
    assert daily.occurrences_after(start, start, _utc(2026, 7, 4, 9)) == days
    assert daily.occurrences_after(start, start, _utc(2026, 7, 4, 9), limit=2) == days[:2]
    assert daily.occurrences_after(start, days[1], _utc(2026, 7, 4, 9), limit=2) == days[2:]
    assert daily.occurrences_after(start, days[-1], days[-1]) == []
    assert Recurrence("").occurrences_after(start, _utc(2026, 6, 1), start) == [start]
    with pytest.raises(ValueError):
        daily.occurrences_after(start, start, days[-1], limit=MAX_OCCURRENCES + 1)


@override_settings(TIME_ZONE="UTC")
def test_occurrences_after_refuses_more_than_the_cap_unless_the_caller_pages() -> None:
    start = _utc(2026, 7, 1)
    secondly = Recurrence("FREQ=SECONDLY")
    with pytest.raises(ValidationError) as refused:
        secondly.occurrences_after(start, start, _utc(2026, 7, 2))
    assert refused.value.code == "too_many_occurrences"
    paged = secondly.occurrences_after(start, start, _utc(2026, 7, 2), limit=MAX_OCCURRENCES)
    assert len(paged) == MAX_OCCURRENCES and paged[0] == _utc(2026, 7, 1, 0, 0) + (paged[1] - paged[0])


@override_settings(TIME_ZONE="UTC")
def test_an_hourly_wall_clock_rule_repeats_or_skips_an_instant_at_a_dst_change() -> None:
    """Brussels: 02:00 local does not exist in spring and happens twice in autumn; hourly wall clocks follow.

    In spring the missing 02:00 lands on the instant of 03:00, which counts once. In
    autumn the repeated 02:00 runs once, leaving a two-hour gap in instants.
    """

    hourly = Recurrence("FREQ=HOURLY")
    spring = _utc(2026, 3, 28, 22)
    spring_moments = hourly.occurrences_after(spring, spring, spring + timedelta(hours=6), zone="Europe/Brussels")
    assert spring_moments == [spring + timedelta(hours=hour) for hour in range(1, 7)]
    autumn = _utc(2026, 10, 24, 22)
    autumn_moments = hourly.occurrences_after(autumn, autumn, autumn + timedelta(hours=6), zone="Europe/Brussels")
    assert autumn_moments == [autumn + timedelta(hours=hour) for hour in (1, 2, 4, 5, 6)]


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
