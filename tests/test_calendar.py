"""Calendar domain contracts: expansion, overrides, overlap, rescheduling, attendees, demo data.

Setup and domain verbs run under ``system_context``, the scope a mutation reaches
after its preflight; ``tests/test_calendar_access.py`` owns who may do what.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from rebac import actor_context, system_context

from angee.calendar.testing.models import Calendar, CalendarEvent, EventAttendee
from angee.messaging.testing.models import Handle, Person
from angee.provenance.context import changeset
from angee.resources.testing.models import Resource
from tests.conftest import create_user

NEW_YORK = ZoneInfo("America/New_York")


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    """Build an aware UTC datetime, the stored and window shape."""

    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _ny(year: int, month: int, day: int, hour: int = 0) -> datetime:
    """Build a New York wall-clock moment as aware UTC."""

    return datetime(year, month, day, hour, tzinfo=NEW_YORK).astimezone(UTC)


def _calendar(*, zone: str = "", overlap_policy: str = "accept", name: str = "Planning") -> Any:
    """Create a calendar for a fresh person."""

    with system_context(reason="tests.calendar.setup"):
        person = Person.objects.create(display_name=f"{name} owner")
        return Calendar.objects.create(party=person, name=name, timezone=zone, overlap_policy=overlap_policy)


def _event(calendar: Any, *, start: datetime, end: datetime, **fields: Any) -> Any:
    """Create one validated event row, as the write path does."""

    with system_context(reason="tests.calendar.setup"):
        event = CalendarEvent(calendar=calendar, start=start, end=end, title=fields.pop("title", "Standup"), **fields)
        event.full_clean()
        event.save()
        return event


def _series(event: Any, window_start: datetime, window_end: datetime) -> list[Any]:
    """Return one series' occurrences (the row and its overrides) as system work."""

    with system_context(reason="tests.calendar.read"):
        rows = CalendarEvent.objects.filter(Q(pk=event.pk) | Q(series_id=event.pk))
        return rows.occurrences(window_start, window_end)


def _occurrences(calendar: Any, window_start: datetime, window_end: datetime) -> list[Any]:
    """Return a calendar's occurrences as system work."""

    with system_context(reason="tests.calendar.read"):
        return CalendarEvent.objects.filter(calendar=calendar).occurrences(window_start, window_end)


# --- expansion ---------------------------------------------------------------


def test_a_weekly_master_yields_one_non_editable_occurrence_per_week(db: None) -> None:
    calendar = _calendar()
    event = _event(calendar, start=_utc(2026, 7, 6, 9), end=_utc(2026, 7, 6, 10), recurrence="FREQ=WEEKLY")

    occurrences = _series(event, _utc(2026, 7, 6), _utc(2026, 8, 5))

    assert [o.start for o in occurrences] == [_utc(2026, 7, d, 9) for d in (6, 13, 20, 27)] + [_utc(2026, 8, 3, 9)]
    assert all(o.editable is False and o.redacted is False for o in occurrences)
    assert all(o.end - o.start == event.end - event.start for o in occurrences)
    assert all(o.calendar == calendar.sqid and o.event_sqid == event.sqid for o in occurrences)
    assert occurrences[0].occurrence_id == f"{event.sqid}:20260706T090000Z"


def test_a_plain_event_yields_one_editable_occurrence_keyed_by_its_sqid(db: None) -> None:
    event = _event(_calendar(), start=_utc(2026, 7, 6, 9), end=_utc(2026, 7, 6, 10))

    [occurrence] = _series(event, _utc(2026, 7, 6), _utc(2026, 7, 7))

    assert (occurrence.occurrence_id, occurrence.editable) == (event.sqid, True)
    assert (occurrence.start, occurrence.end) == (_utc(2026, 7, 6, 9), _utc(2026, 7, 6, 10))


def test_the_window_is_half_open_and_takes_every_occurrence_that_meets_it(db: None) -> None:
    calendar = _calendar()
    event = _event(calendar, start=_utc(2026, 7, 6, 9), end=_utc(2026, 7, 6, 10), recurrence="FREQ=WEEKLY")

    starts = [o.start for o in _series(event, _utc(2026, 7, 6, 9), _utc(2026, 7, 27, 9))]
    # An instance already running at the window start meets the window.
    running = [o.start for o in _series(event, _utc(2026, 7, 13, 9, 30), _utc(2026, 7, 13, 11))]

    assert starts == [_utc(2026, 7, 6, 9), _utc(2026, 7, 13, 9), _utc(2026, 7, 20, 9)]
    assert running == [_utc(2026, 7, 13, 9)]


def test_occurrences_flatten_and_sort_across_rows(db: None) -> None:
    calendar = _calendar()
    _event(calendar, title="Weekly", start=_utc(2026, 7, 6, 9), end=_utc(2026, 7, 6, 10), recurrence="FREQ=WEEKLY")
    _event(calendar, title="One-off", start=_utc(2026, 7, 8, 14), end=_utc(2026, 7, 8, 15))

    occurrences = _occurrences(calendar, _utc(2026, 7, 6), _utc(2026, 7, 14))

    assert [(o.title, o.start) for o in occurrences] == [
        ("Weekly", _utc(2026, 7, 6, 9)),
        ("One-off", _utc(2026, 7, 8, 14)),
        ("Weekly", _utc(2026, 7, 13, 9)),
    ]


def test_a_cancelled_event_shows_no_occurrence(db: None) -> None:
    calendar = _calendar()
    _event(calendar, start=_utc(2026, 7, 6, 9), end=_utc(2026, 7, 6, 10), status="cancelled")

    assert _occurrences(calendar, _utc(2026, 7, 6), _utc(2026, 7, 7)) == []


def test_an_all_day_occurrence_ends_on_the_next_local_midnight_across_spring_forward(db: None) -> None:
    calendar = _calendar(zone="America/New_York")
    event = _event(calendar, all_day=True, recurrence="FREQ=DAILY", start=_ny(2026, 3, 7), end=_ny(2026, 3, 8))

    occurrences = _series(event, _ny(2026, 3, 7), _ny(2026, 3, 10))
    by_date = {o.start.astimezone(NEW_YORK).date(): o for o in occurrences}

    # 2026-03-08 is the US spring-forward: an absolute timedelta would end at 01:00.
    spring_forward = by_date[date(2026, 3, 8)]
    assert spring_forward.end.astimezone(NEW_YORK).date() == date(2026, 3, 9)
    assert all(o.end.astimezone(NEW_YORK).time() == time(0, 0) for o in occurrences)


# --- overrides ---------------------------------------------------------------


@pytest.fixture
def dst_series(db: None) -> tuple[Any, Any, Any, Any]:
    """A Monday 09:00 New York standup across the spring-forward, one instance moved and one cancelled."""

    del db
    calendar = _calendar(zone="America/New_York")
    master = _event(calendar, start=_ny(2026, 3, 2, 9), end=_ny(2026, 3, 2, 10), recurrence="FREQ=WEEKLY")
    moved = _event(
        calendar, series=master, original_start=_ny(2026, 3, 9, 9), title="Standup (moved)",
        start=_ny(2026, 3, 10, 11), end=_ny(2026, 3, 10, 12),
    )
    cancelled = _event(
        calendar, series=master, original_start=_ny(2026, 3, 16, 9), status="cancelled",
        start=_ny(2026, 3, 16, 9), end=_ny(2026, 3, 16, 10),
    )
    return calendar, master, moved, cancelled


def test_overrides_replace_and_drop_their_instances_across_a_dst_change(dst_series: tuple[Any, ...]) -> None:
    calendar, master, moved, _cancelled = dst_series

    occurrences = _occurrences(calendar, _ny(2026, 3, 1), _ny(2026, 3, 25))

    # 09:00 local stays 09:00 across the shift: 14:00Z before it, 13:00Z after. The moved
    # instance keeps the id of the 9 March instance it replaces and opens the override.
    assert [(o.occurrence_id, o.event_sqid, o.start) for o in occurrences] == [
        (f"{master.sqid}:20260302T140000Z", master.sqid, _utc(2026, 3, 2, 14)),
        (f"{master.sqid}:20260309T130000Z", moved.sqid, _utc(2026, 3, 10, 15)),
        (f"{master.sqid}:20260323T130000Z", master.sqid, _utc(2026, 3, 23, 13)),
    ]
    assert [o.editable for o in occurrences] == [False, True, False]


def test_a_moved_instance_leaves_its_original_window_and_shows_in_its_new_one(dst_series: tuple[Any, ...]) -> None:
    calendar, _master, moved, _cancelled = dst_series

    assert _occurrences(calendar, _ny(2026, 3, 9), _ny(2026, 3, 9, 12)) == []
    assert [o.event_sqid for o in _occurrences(calendar, _ny(2026, 3, 10), _ny(2026, 3, 11))] == [moved.sqid]


def test_a_series_expansion_includes_its_overrides(dst_series: tuple[Any, ...]) -> None:
    _calendar_, master, moved, _cancelled = dst_series

    assert [o.event_sqid for o in _series(master, _ny(2026, 3, 8), _ny(2026, 3, 18))] == [moved.sqid]


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        pytest.param({"recurrence": "FREQ=DAILY"}, "recurrence", id="an-override-carries-no-rule"),
        pytest.param({"original_start": _utc(2026, 3, 10, 13)}, "original_start", id="not-an-instance"),
        pytest.param({"original_start": None}, "original_start", id="names-its-instance"),
    ],
)
def test_an_override_must_fit_its_series(
    dst_series: tuple[Any, ...], fields: dict[str, Any], error: str,
) -> None:
    calendar, master, _moved, _cancelled = dst_series
    values = {"original_start": _ny(2026, 3, 23, 9), **fields}

    with system_context(reason="tests.calendar.validate"), pytest.raises(ValidationError) as raised:
        CalendarEvent(
            calendar=calendar, series=master, title="Bad", start=_utc(2026, 3, 24, 9), end=_utc(2026, 3, 24, 10),
            **values,
        ).full_clean()

    assert error in raised.value.message_dict


def test_an_override_needs_a_recurring_master_on_its_own_calendar(dst_series: tuple[Any, ...]) -> None:
    calendar, master, moved, _cancelled = dst_series
    other = _calendar(name="Other")

    with system_context(reason="tests.calendar.validate"):
        for series, on, field in ((moved, calendar, "series"), (master, other, "calendar")):
            with pytest.raises(ValidationError) as raised:
                CalendarEvent(
                    calendar=on, series=series, original_start=_ny(2026, 3, 23, 9), title="Bad",
                    start=_utc(2026, 3, 24, 9), end=_utc(2026, 3, 24, 10),
                ).full_clean()
            assert field in raised.value.message_dict


def test_one_override_per_instance(dst_series: tuple[Any, ...]) -> None:
    calendar, master, _moved, _cancelled = dst_series

    with system_context(reason="tests.calendar.validate"), pytest.raises(IntegrityError), transaction.atomic():
        CalendarEvent.objects.create(
            calendar=calendar, series=master, original_start=_ny(2026, 3, 9, 9), title="Twice",
            start=_ny(2026, 3, 9, 9), end=_ny(2026, 3, 9, 10),
        )


# --- a series owns its overrides ---------------------------------------------


def _save(row: Any, **fields: Any) -> None:
    with system_context(reason="tests.calendar.edit"):
        for name, value in fields.items():
            setattr(row, name, value)
        row.full_clean()
        row.save()


def _refreshed(*rows: Any) -> None:
    with system_context(reason="tests.calendar.read"):
        for row in rows:
            row.refresh_from_db()


def test_moving_a_master_carries_its_overrides_by_the_wall_clock_change(dst_series: tuple[Any, ...]) -> None:
    calendar, master, moved, cancelled = dst_series

    _save(master, start=_ny(2026, 3, 2, 10), end=_ny(2026, 3, 2, 11))
    _refreshed(moved, cancelled)

    # 10:00 local on either side of the spring-forward.
    assert (moved.original_start, cancelled.original_start) == (_ny(2026, 3, 9, 10), _ny(2026, 3, 16, 10))
    assert [(o.event_sqid, o.start) for o in _occurrences(calendar, _ny(2026, 3, 1), _ny(2026, 3, 25))] == [
        (master.sqid, _ny(2026, 3, 2, 10)),
        (moved.sqid, _ny(2026, 3, 10, 11)),
        (master.sqid, _ny(2026, 3, 23, 10)),
    ]


def test_changing_the_calendar_zone_keeps_overridden_instances_overridden(dst_series: tuple[Any, ...]) -> None:
    calendar, master, moved, cancelled = dst_series
    london = ZoneInfo("Europe/London")

    _save(calendar, timezone="Europe/London")
    _refreshed(moved, cancelled)

    # The series now runs at 14:00 London (its UTC start), and so do its overridden instances.
    assert cancelled.original_start == datetime(2026, 3, 16, 14, tzinfo=london).astimezone(UTC)
    assert [(o.event_sqid, o.start) for o in _occurrences(calendar, _utc(2026, 3, 1), _utc(2026, 3, 25))] == [
        (master.sqid, _utc(2026, 3, 2, 14)),
        (moved.sqid, _ny(2026, 3, 10, 11)),
        (master.sqid, _utc(2026, 3, 23, 14)),
    ]


def test_clearing_the_rule_detaches_edited_overrides_and_drops_cancellations(dst_series: tuple[Any, ...]) -> None:
    calendar, master, moved, cancelled = dst_series

    _save(master, recurrence="")
    _refreshed(moved)

    assert (moved.series_id, moved.original_start, moved.title) == (None, None, "Standup (moved)")
    with system_context(reason="tests.calendar.read"):
        assert not CalendarEvent.objects.filter(pk=cancelled.pk).exists()
    shown = _occurrences(calendar, _ny(2026, 3, 1), _ny(2026, 3, 25))
    assert [o.event_sqid for o in shown] == [master.sqid, moved.sqid]


def test_a_cancelled_master_hides_its_moved_instances_from_the_calendar_and_from_overlap(db: None) -> None:
    del db
    calendar = _calendar(overlap_policy="refuse")
    master = _event(calendar, start=_utc(2026, 7, 6, 9), end=_utc(2026, 7, 6, 10), recurrence="FREQ=WEEKLY")
    _event(
        calendar, series=master, original_start=_utc(2026, 7, 13, 9), title="Moved",
        start=_utc(2026, 7, 14, 9), end=_utc(2026, 7, 14, 10),
    )

    _save(master, status="cancelled")

    assert _occurrences(calendar, _utc(2026, 7, 1), _utc(2026, 7, 31)) == []
    _clean(calendar, _utc(2026, 7, 14, 9), _utc(2026, 7, 14, 10))


def test_an_event_stays_on_its_calendar_series_and_instance(dst_series: tuple[Any, ...]) -> None:
    calendar, master, moved, _cancelled = dst_series
    other = _calendar(name="Other")

    for field, value in (("calendar", other), ("series", None), ("original_start", _ny(2026, 3, 23, 9))):
        with system_context(reason="tests.calendar.edit"), pytest.raises(ValidationError) as raised:
            setattr(moved, field, value)
            moved.save()
        assert field in raised.value.message_dict
        _refreshed(moved)
    with system_context(reason="tests.calendar.edit"), pytest.raises(ValidationError):
        attendee = EventAttendee.objects.create(
            event=master, handle=Handle.objects.create(platform="email", value="kept@example.test"),
        )
        attendee.event = moved
        attendee.save()


# --- overlap -----------------------------------------------------------------


def _clean(calendar: Any, start: datetime, end: datetime, **fields: Any) -> None:
    with system_context(reason="tests.calendar.validate"):
        CalendarEvent(calendar=calendar, title="New", start=start, end=end, **fields).full_clean()


@pytest.mark.parametrize(
    ("existing", "candidate", "refused"),
    [
        pytest.param({}, {}, True, id="busy-meets-busy"),
        pytest.param({"transparency": "free"}, {}, False, id="a-free-event-never-blocks"),
        pytest.param({"status": "cancelled"}, {}, False, id="a-cancelled-event-never-blocks"),
        pytest.param({}, {"transparency": "free"}, False, id="a-free-candidate-is-never-refused"),
        pytest.param({"recurrence": "FREQ=WEEKLY", "start_week": -1}, {}, True, id="an-expanded-recurrence-blocks"),
    ],
)
def test_a_refuse_calendar_rejects_an_overlap_of_busy_time(
    db: None, existing: dict[str, Any], candidate: dict[str, Any], refused: bool,
) -> None:
    calendar = _calendar(overlap_policy="refuse")
    fields = dict(existing)
    shift = fields.pop("start_week", 0) * 7
    _event(calendar, start=_utc(2026, 7, 13 + shift, 10), end=_utc(2026, 7, 13 + shift, 11), **fields)

    if refused:
        with pytest.raises(ValidationError) as raised:
            _clean(calendar, _utc(2026, 7, 13, 10, 30), _utc(2026, 7, 13, 11, 30), **candidate)
        assert "start" in raised.value.message_dict
    else:
        _clean(calendar, _utc(2026, 7, 13, 10, 30), _utc(2026, 7, 13, 11, 30), **candidate)


def test_an_accept_calendar_takes_an_overlap_and_adjacent_slots_never_overlap(db: None) -> None:
    accepting = _calendar()
    refusing = _calendar(overlap_policy="refuse", name="Refusing")
    for calendar in (accepting, refusing):
        _event(calendar, start=_utc(2026, 7, 13, 10), end=_utc(2026, 7, 13, 11))

    _clean(accepting, _utc(2026, 7, 13, 10, 30), _utc(2026, 7, 13, 11, 30))
    _clean(refusing, _utc(2026, 7, 13, 11), _utc(2026, 7, 13, 12))


def test_an_override_does_not_conflict_with_the_instance_it_replaces(db: None) -> None:
    calendar = _calendar(overlap_policy="refuse")
    master = _event(calendar, start=_utc(2026, 7, 6, 9), end=_utc(2026, 7, 6, 10), recurrence="FREQ=WEEKLY")

    override = _event(
        calendar, series=master, original_start=_utc(2026, 7, 13, 9), title="Longer standup",
        start=_utc(2026, 7, 13, 9), end=_utc(2026, 7, 13, 10, 30),
    )
    with system_context(reason="tests.calendar.validate"):
        override.full_clean()
        master.full_clean()
        # Moving an instance onto the next week's instance is a real conflict.
        with pytest.raises(ValidationError):
            override.reschedule(_utc(2026, 7, 20, 9, 15), _utc(2026, 7, 20, 9, 45))


def test_a_new_series_is_checked_instance_by_instance_up_to_the_horizon(db: None) -> None:
    del db
    calendar = _calendar(overlap_policy="refuse")
    _event(calendar, title="One-off", start=_utc(2026, 7, 20, 9), end=_utc(2026, 7, 20, 10))
    _event(calendar, title="Far off", start=_utc(2029, 7, 2, 9), end=_utc(2029, 7, 2, 10))

    # The third weekly instance meets the one-off.
    with pytest.raises(ValidationError):
        _clean(calendar, _utc(2026, 7, 6, 9), _utc(2026, 7, 6, 10), recurrence="FREQ=WEEKLY")
    # A series ending before it does not; an open-ended one is checked two years out only.
    _clean(calendar, _utc(2026, 7, 6, 9), _utc(2026, 7, 6, 10), recurrence="FREQ=WEEKLY;COUNT=2")
    _clean(calendar, _utc(2026, 7, 7, 9), _utc(2026, 7, 7, 10), recurrence="FREQ=WEEKLY")


def test_an_event_zone_keeps_its_wall_clock_on_a_calendar_in_another_zone(db: None) -> None:
    """A Monday 09:00 Berlin series on a New York calendar stays 09:00 Berlin across both DST changes."""

    del db
    berlin = ZoneInfo("Europe/Berlin")
    calendar = _calendar(zone="America/New_York")
    start = datetime(2026, 3, 2, 9, tzinfo=berlin).astimezone(UTC)
    series = _event(
        calendar, start=start, end=start + timedelta(hours=1), recurrence="FREQ=WEEKLY", timezone="Europe/Berlin",
    )

    # New York springs forward on 8 March, Berlin on 29 March.
    occurrences = _occurrences(calendar, _utc(2026, 3, 1), _utc(2026, 4, 7))

    assert series.expansion_zone == "Europe/Berlin"
    assert [o.start.astimezone(berlin).strftime("%m-%d %H:%M") for o in occurrences] == [
        "03-02 09:00", "03-09 09:00", "03-16 09:00", "03-23 09:00", "03-30 09:00", "04-06 09:00",
    ]
    # A calendar zone change leaves a series with its own zone where it is.
    _save(calendar, timezone="Europe/London")
    after = _occurrences(calendar, _utc(2026, 3, 1), _utc(2026, 4, 7))
    assert [o.start for o in after] == [o.start for o in occurrences]


# --- bounds ------------------------------------------------------------------


def test_a_bounded_series_stores_its_end_and_reads_skip_it_once_ended(db: None) -> None:
    del db
    calendar = _calendar()
    bounded = _event(calendar, start=_utc(2026, 7, 6, 9), end=_utc(2026, 7, 6, 10), recurrence="FREQ=WEEKLY;COUNT=3")
    endless = _event(calendar, start=_utc(2026, 7, 6, 11), end=_utc(2026, 7, 6, 12), recurrence="FREQ=WEEKLY")

    assert (bounded.series_end, endless.series_end) == (_utc(2026, 7, 20, 10), None)
    assert [o.event_sqid for o in _occurrences(calendar, _utc(2026, 8, 3), _utc(2026, 8, 4))] == [endless.sqid]
    _save(bounded, recurrence="FREQ=WEEKLY;COUNT=5")
    assert bounded.series_end == _utc(2026, 8, 3, 10)


def test_an_occurrence_window_spans_at_most_four_hundred_days(db: None) -> None:
    calendar = _calendar()

    with pytest.raises(ValidationError):
        _occurrences(calendar, _utc(2026, 1, 1), _utc(2027, 2, 6))
    assert _occurrences(calendar, _utc(2026, 1, 1), _utc(2027, 2, 5)) == []


# --- all-day events ----------------------------------------------------------


def test_an_all_day_event_starts_and_ends_at_local_midnight(db: None) -> None:
    calendar = _calendar(zone="America/New_York")

    # A UTC-midnight client sends 20:00 the evening before, in New York.
    with pytest.raises(ValidationError) as raised:
        _clean(calendar, _utc(2026, 7, 7), _utc(2026, 7, 8), all_day=True)
    assert "start" in raised.value.message_dict
    _clean(calendar, _ny(2026, 7, 7), _ny(2026, 7, 8), all_day=True)


def test_dragging_an_all_day_event_lands_on_the_nearest_local_midnights(db: None) -> None:
    calendar = _calendar(zone="America/New_York")
    holiday = _event(calendar, title="Holiday", all_day=True, start=_ny(2026, 7, 7), end=_ny(2026, 7, 8))

    with system_context(reason="tests.calendar.reschedule"):
        # The viewer's own midnight in UTC, four hours off New York's.
        holiday.reschedule(_utc(2026, 7, 9), _utc(2026, 7, 10))

    assert (holiday.start, holiday.end) == (_ny(2026, 7, 9), _ny(2026, 7, 10))


# --- override defaults -------------------------------------------------------


def test_an_override_takes_its_masters_visibility_and_transparency_by_default(db: None) -> None:
    calendar = _calendar()
    master = _event(
        calendar, start=_utc(2026, 7, 6, 9), end=_utc(2026, 7, 6, 10), recurrence="FREQ=WEEKLY",
        visibility="private", transparency="free",
    )

    override = _event(
        calendar, series=master, original_start=_utc(2026, 7, 13, 9), start=_utc(2026, 7, 13, 10),
        end=_utc(2026, 7, 13, 11),
    )

    assert (override.visibility, override.transparency) == ("private", "free")


# --- rescheduling ------------------------------------------------------------


def test_reschedule_moves_a_plain_event_and_records_its_history(db: None) -> None:
    event = _event(_calendar(), start=_utc(2026, 7, 6, 9), end=_utc(2026, 7, 6, 10))
    before = event.history.count()
    reason = "tests.calendar.reschedule"

    with changeset(fresh=True, change_reason=reason), system_context(reason=reason):
        event.reschedule(_utc(2026, 7, 7, 11), _utc(2026, 7, 7, 12))
        event.refresh_from_db()
    recorded = event.history.filter(changeset__change_reason=reason).latest("history_id")

    assert (event.start, event.end) == (_utc(2026, 7, 7, 11), _utc(2026, 7, 7, 12))
    assert event.history.count() == before + 1
    assert (recorded.start, recorded.end, recorded.history_change_reason) == (event.start, event.end, reason)


@pytest.mark.parametrize(
    ("recurrence", "start", "end"),
    [
        pytest.param("FREQ=WEEKLY", _utc(2026, 7, 7, 9), _utc(2026, 7, 7, 10), id="a-recurring-master"),
        pytest.param("", _utc(2026, 7, 6, 12), _utc(2026, 7, 6, 11), id="an-inverted-interval"),
    ],
)
def test_reschedule_refuses_and_leaves_the_row_unchanged(
    db: None, recurrence: str, start: datetime, end: datetime,
) -> None:
    event = _event(_calendar(), start=_utc(2026, 7, 6, 9), end=_utc(2026, 7, 6, 10), recurrence=recurrence)

    with system_context(reason="tests.calendar.reschedule"):
        with pytest.raises(ValidationError):
            event.reschedule(start, end)
        event.refresh_from_db()

    assert (event.start, event.end) == (_utc(2026, 7, 6, 9), _utc(2026, 7, 6, 10))


# --- attendees ---------------------------------------------------------------


def test_merging_two_invited_handles_keeps_one_invitation_with_the_organizer_and_the_stronger_answer(
    db: None,
) -> None:
    del db
    event = _event(_calendar(), start=_utc(2026, 7, 6, 9), end=_utc(2026, 7, 6, 10))
    with system_context(reason="tests.calendar.merge"):
        owner = create_user("cal_merger")
        kept = Handle.objects.create(platform="email", value="ada@example.test", created_by=owner)
        merged = Handle.objects.create(platform="email", value="ada.l@example.test", created_by=owner)
        EventAttendee.objects.create(event=event, handle=kept)
        EventAttendee.objects.create(event=event, handle=merged, role="organizer", response="accepted")

        kept.with_actor(owner).merge(records=[merged.public_id])
        attendees = list(EventAttendee.objects.filter(event=event).values_list("handle_id", "role", "response"))

    assert attendees == [(kept.pk, "organizer", "accepted")]


@pytest.fixture
def invitation(db: None) -> tuple[Any, Any, Any]:
    del db
    event = _event(_calendar(), start=_utc(2026, 7, 6, 9), end=_utc(2026, 7, 6, 10))
    with system_context(reason="tests.calendar.setup"):
        ada = Handle.objects.create(platform="email", value="ada@example.test")
        grace = Handle.objects.create(platform="email", value="grace@example.test")
    return event, ada, grace


def test_an_attendee_is_a_required_invitation_awaiting_a_reply(invitation: tuple[Any, Any, Any]) -> None:
    event, ada, _grace = invitation

    with system_context(reason="tests.calendar.attendee"):
        attendee = EventAttendee.objects.create(event=event, handle=ada)
        assert list(event.attendees.all()) == [ada]

    assert (attendee.role, attendee.response) == ("required", "needs_action")


@pytest.mark.parametrize(
    ("second", "role"),
    [
        pytest.param("ada", "required", id="one-row-per-handle"),
        pytest.param("grace", "organizer", id="at-most-one-organizer"),
    ],
)
def test_attendee_uniqueness(invitation: tuple[Any, Any, Any], second: str, role: str) -> None:
    event, ada, grace = invitation

    with system_context(reason="tests.calendar.attendee"):
        EventAttendee.objects.create(event=event, handle=ada, role="organizer")
        with pytest.raises(IntegrityError), transaction.atomic():
            EventAttendee.objects.create(event=event, handle={"ada": ada, "grace": grace}[second], role=role)


# --- demo resources ----------------------------------------------------------


def _load_demo() -> Any:
    return Resource.objects.load_addons(
        (apps.get_app_config("iam"), apps.get_app_config("calendar")),
        tiers=[Resource.Tier.DEMO],
        allow_non_dev=True,
    )


def test_demo_resources_load_share_and_reload_idempotently(db: None) -> None:
    del db
    _load_demo()
    with system_context(reason="tests.calendar.demo"):
        alice = apps.get_model("iam", "User").objects.get(username="alice")
        bob = apps.get_model("iam", "User").objects.get(username="bob")
        alice_calendar = Calendar.objects.get(party__person__user=alice)

    window = (_utc(2026, 7, 6), _utc(2026, 7, 27))
    alice_view = CalendarEvent.objects.with_actor(alice).occurrences(*window)
    bob_view = CalendarEvent.objects.with_actor(bob).filter(calendar=alice_calendar).occurrences(*window)

    # The standup's 13 July instance moved to the 14th and its 20 July one is cancelled.
    assert [o.title for o in bob_view] == [
        "Team Standup",
        "Project Kickoff",
        "Team Standup (moved)",
        "Quarterly Review",
    ]
    # Alice sees her own calendar and Bob's private focus block as busy time only.
    assert [(o.title, o.redacted) for o in alice_view if o.calendar != alice_calendar.sqid] == [("", True)]
    with system_context(reason="tests.calendar.demo"):
        organizers = EventAttendee.objects.filter(role="organizer").count()
    assert organizers == 2

    again = _load_demo()
    assert (again.created, again.updated) == (0, 0)


def test_a_user_gives_their_own_person_a_calendar_and_holds_it(db: None) -> None:
    del db
    with system_context(reason="tests.calendar.setup"):
        user = create_user("cal_self")
        person = Person.objects.for_user(user)

    with actor_context(user):
        calendar = Calendar.objects.create(party=person, name="Mine")

    assert list(Calendar.objects.with_actor(user).filter(pk=calendar.pk)) == [calendar]
