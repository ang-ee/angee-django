"""Due rules: resolution from named anchors, status on read, repeat, and the stored field.

Due rules are pure values (no model, no database), so these tests touch no rows.
Anchors carry explicit zones; ``America/New_York`` crosses a DST change and
``America/Barbados`` keeps one offset (UTC-4) all year.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
import strawberry
import strawberry_django
from django.core.exceptions import ValidationError
from django.db import migrations, models
from django.db.migrations.state import ModelState
from django.db.migrations.writer import MigrationWriter
from django.forms import modelform_factory
from django.test import override_settings
from django.test.utils import isolate_apps
from hypothesis import example, given, settings
from hypothesis import strategies as st

from angee.base.serialization import canonical_json
from angee.graphql.data.resource_fields import resource_field_widget
from angee.scheduling.due import (
    Align,
    Anchor,
    Counter,
    Due,
    DueRule,
    DueStatus,
    Offset,
    Repeat,
    Term,
)
from angee.scheduling.fields import DueRuleField

NEW_YORK = ZoneInfo("America/New_York")
BARBADOS = ZoneInfo("America/Barbados")
WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")

with isolate_apps("angee.scheduling"):

    class _Item(models.Model):
        """A row composing the due-rule field beside its own date and basis."""

        due_date = models.DateField(null=True, blank=True)
        due_rule = DueRuleField()
        due_basis = models.JSONField(default=dict, blank=True)

        class Meta:
            app_label = "scheduling"


@strawberry_django.type(_Item)
class _ItemType:
    due_rule: strawberry.auto


@strawberry_django.input(_Item)
class _ItemInput:
    due_rule: strawberry.auto


@strawberry.type
class _ItemQuery:
    @strawberry.field
    def item(self) -> _ItemType:
        return _Item(due_rule=DueRule.of(Term(anchor="created", offset=Offset(7, "days"))).as_json())


@strawberry.type
class _ItemMutation:
    @strawberry.mutation
    def check_item(self, data: _ItemInput) -> _ItemType:
        item = _Item(due_rule=data.due_rule)
        item.full_clean(exclude=["due_date", "due_basis"])
        return item


def _at(zone: ZoneInfo, *parts: int) -> Anchor:
    """An instant anchor on the wall clock of ``zone``."""

    return Anchor(at=datetime(*parts, tzinfo=zone), zone=zone.key)


# --- worked examples -----------------------------------------------------------------------------------------


def test_hours_before_an_anchor_are_elapsed_time_across_a_dst_change() -> None:
    """48 hours before a slot is 48 real hours: across spring-forward the wall clock moves an hour."""

    rule = DueRule.of(Term(anchor="first_slot", offset=Offset(-48, "hours")))
    due = rule.resolve(anchors={"first_slot": _at(NEW_YORK, 2026, 3, 9, 19)})

    assert due.at == datetime(2026, 3, 9, 19, tzinfo=NEW_YORK).astimezone(UTC) - timedelta(hours=48)
    assert due.at.astimezone(NEW_YORK) == datetime(2026, 3, 7, 18, tzinfo=NEW_YORK)
    assert due.local_date == date(2026, 3, 7)
    assert due.pending == ()


def test_days_after_an_anchor_keep_the_wall_clock_across_a_dst_change() -> None:
    """7 days after creation keeps 10:00 local, so the elapsed time is 7 days less an hour."""

    created = _at(NEW_YORK, 2026, 3, 5, 10)
    due = DueRule.of(Term(anchor="created", offset=Offset(7, "days"))).resolve(anchors={"created": created})

    assert due.at is not None
    assert due.at.astimezone(NEW_YORK) == datetime(2026, 3, 12, 10, tzinfo=NEW_YORK)
    assert created.at is not None
    assert due.at - created.at == timedelta(days=7, hours=-1)


@pytest.mark.parametrize(
    ("slot", "monday"),
    [
        pytest.param(
            datetime(2026, 3, 16, 20, tzinfo=BARBADOS), date(2026, 3, 23), id="a-monday-gives-the-next-monday"
        ),
        pytest.param(datetime(2026, 3, 18, 9, tzinfo=BARBADOS), date(2026, 3, 23), id="a-wednesday"),
        pytest.param(
            datetime(2026, 3, 15, 23, 30, tzinfo=BARBADOS), date(2026, 3, 16), id="sunday-night-local-is-monday-utc"
        ),
    ],
)
def test_the_monday_strictly_after_the_last_slot_counts_in_the_anchor_zone(slot: datetime, monday: date) -> None:
    rule = DueRule.of(Term(anchor="last_slot", align=[Align.next_weekday("MO", strict=True)]))
    due = rule.resolve(anchors={"last_slot": Anchor(at=slot, zone="America/Barbados")})

    assert due.local_date == monday


@pytest.mark.parametrize(
    "start",
    [
        pytest.param(Anchor(on=date(2026, 3, 14), zone="America/Barbados"), id="a-local-day"),
        pytest.param(_at(BARBADOS, 2026, 3, 14, 9, 30), id="an-instant"),
    ],
)
@override_settings(TIME_ZONE="UTC")
def test_a_local_time_lands_in_the_anchor_zone(start: Anchor) -> None:
    """By 2 p.m. venue time: 14:00 on the anchor's local day in the anchor's zone, not the project's."""

    due = DueRule.of(Term(anchor="start_day", align=[Align.at_time("14:00")])).resolve(anchors={"start_day": start})

    assert due.at == datetime(2026, 3, 14, 18, tzinfo=UTC)
    assert due.local_date == date(2026, 3, 14)


def test_a_named_zone_on_the_local_time_overrides_the_anchor_zone() -> None:
    rule = DueRule.of(Term(anchor="start_day", align=[Align.at_time("14:00", zone="America/Barbados")]))
    due = rule.resolve(anchors={"start_day": Anchor(on=date(2026, 7, 1), zone="America/New_York")})

    assert due.at == datetime(2026, 7, 1, 18, tzinfo=UTC)


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        pytest.param(Offset(-45, "days"), date(2026, 4, 17), id="45-days-before"),
        pytest.param(Offset(7, "days"), date(2026, 6, 8), id="7-days-after"),
        pytest.param(Offset(-2, "weeks"), date(2026, 5, 18), id="2-weeks-before"),
    ],
)
def test_day_offsets_from_a_local_day_stay_local_days(offset: Offset, expected: date) -> None:
    due = DueRule.of(Term(anchor="start_day", offset=offset)).resolve(anchors={"start_day": date(2026, 6, 1)})

    assert (due.at, due.local_date) == (None, expected)


@pytest.mark.parametrize(
    ("anchor", "term", "expected"),
    [
        pytest.param(date(2026, 1, 31), Term(anchor="a", offset=Offset(1, "months")), date(2026, 2, 28), id="clamp"),
        pytest.param(
            date(2024, 5, 2),
            Term(anchor="a", offset=Offset(24, "months"), align=[Align.end_of("month")]),
            date(2026, 5, 31),
            id="months-then-end-of-month",
        ),
        pytest.param(
            date(2026, 5, 13), Term(anchor="a", align=[Align.start_of("week")]), date(2026, 5, 11), id="iso-week"
        ),
        pytest.param(
            date(2026, 5, 13), Term(anchor="a", align=[Align.end_of("quarter")]), date(2026, 6, 30), id="quarter"
        ),
        pytest.param(date(2026, 5, 13), Term(anchor="a", align=[Align.start_of("year")]), date(2026, 1, 1), id="year"),
        pytest.param(
            date(2026, 5, 13), Term(anchor="a", align=[Align.previous_weekday("FR")]), date(2026, 5, 8), id="previous"
        ),
        pytest.param(
            date(2026, 5, 15),
            Term(anchor="a", align=[Align.previous_weekday("FR", strict=True)]),
            date(2026, 5, 8),
            id="strictly-previous",
        ),
    ],
)
def test_calendar_steps_and_alignments_on_a_local_day(anchor: date, term: Term, expected: date) -> None:
    assert DueRule.of(term).resolve(anchors={"a": anchor}).local_date == expected


def test_end_of_day_on_an_instant_is_its_last_local_instant() -> None:
    rule = DueRule.of(Term(anchor="created", offset=Offset(3, "days"), align=[Align.end_of("day")]))
    due = rule.resolve(anchors={"created": _at(BARBADOS, 2026, 5, 4, 9)})

    assert due.at == datetime(2026, 5, 8, 3, 59, 59, 999999, tzinfo=UTC)
    assert due.local_date == date(2026, 5, 7)


@override_settings(TIME_ZONE="America/New_York")
def test_an_anchor_without_a_zone_steps_in_the_project_zone() -> None:
    due = DueRule.of(Term(anchor="created", align=[Align.end_of("day")])).resolve(
        anchors={"created": datetime(2026, 5, 5, 2, tzinfo=UTC)}
    )

    assert due.local_date == date(2026, 5, 4)
    assert due.terms[0].zone == "America/New_York"


# --- pending -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("anchors", [{}, {"first_slot": None}], ids=["missing", "none"])
def test_an_unknown_anchor_leaves_the_rule_pending(anchors: dict[str, None]) -> None:
    due = DueRule.of(Term(anchor="first_slot", offset=Offset(-48, "hours"))).resolve(anchors=anchors)

    assert due.pending == ("first_slot",)
    assert (due.at, due.local_date, due.winner) == (None, None, None)
    assert due.status(now=datetime(2026, 1, 1, tzinfo=UTC)) is None


def test_one_pending_term_leaves_a_combined_rule_without_a_point() -> None:
    rule = DueRule.of(
        Term(label="soon", anchor="created", offset=Offset(30, "days")),
        Term(label="before", anchor="first_slot", offset=Offset(-2, "days")),
    )
    due = rule.resolve(anchors={"created": date(2026, 1, 1)})

    assert due.pending == ("first_slot",)
    assert due.local_date is None
    assert due.terms[0].local_date == date(2026, 1, 31)


# --- combination, counters and forecast ----------------------------------------------------------------------

_MAINTAINED = DueRule(
    combine="earliest",
    repeat=Repeat.from_completion(),
    terms=[
        Term(
            label="calendar",
            anchor="last_done",
            offset=Offset(24, "months"),
            align=[Align.end_of("month")],
            warn=Offset(30, "days"),
        ),
        Term(
            label="usage",
            anchor="last_done",
            counter="usage_hours",
            offset=Offset(300),
            grace=Offset(10),
            warn=Offset(50),
        ),
    ],
)
_LAST_DONE = Anchor.reading(on=date(2024, 5, 2), zone="UTC", readings={"usage_hours": Decimal("6357.7")})
_READ_AT = datetime(2026, 1, 15, 12, tzinfo=UTC)


def _usage(reading: str, rate: str | None = None) -> dict[str, Counter]:
    return {"usage_hours": Counter(reading=Decimal(reading), at=_READ_AT, rate_per_day=rate)}


def test_a_forecast_counter_competes_with_the_calendar_and_wins_when_first() -> None:
    """171.75 remaining hours at 2.5 a day is 68.7 days: before the calendar limit at the end of May."""

    due = _MAINTAINED.resolve(anchors={"last_done": _LAST_DONE}, counters=_usage("6485.95", "2.5"))

    assert due.winner == "usage"
    assert due.at == _READ_AT + timedelta(days=68, hours=16, minutes=48)
    assert due.local_date == date(2026, 3, 25)
    assert due.counter_targets == {"usage_hours": Decimal("6657.7")}
    assert due.terms[0].local_date == date(2026, 5, 31)


def test_a_counter_without_a_rate_reports_its_target_beside_the_calendar_point() -> None:
    due = _MAINTAINED.resolve(anchors={"last_done": _LAST_DONE}, counters=_usage("6485.95"))

    assert (due.winner, due.at, due.local_date) == ("calendar", None, date(2026, 5, 31))
    assert due.counter_targets == {"usage_hours": Decimal("6657.7")}


def test_latest_takes_whichever_comes_last() -> None:
    rule = _MAINTAINED.model_copy(update={"combine": "latest"})
    due = rule.resolve(anchors={"last_done": _LAST_DONE}, counters=_usage("6485.95", "2.5"))

    assert (due.winner, due.local_date) == ("calendar", date(2026, 5, 31))


def test_an_instant_on_a_due_day_comes_before_the_day_itself() -> None:
    rule = DueRule.of(
        Term(label="day", anchor="start_day"),
        Term(label="afternoon", anchor="start_day", align=[Align.at_time("14:00")]),
    )
    due = rule.resolve(anchors={"start_day": Anchor(on=date(2026, 3, 14), zone="America/Barbados")})

    assert (due.winner, due.at) == ("afternoon", datetime(2026, 3, 14, 18, tzinfo=UTC))


@pytest.mark.parametrize(
    ("today", "reading", "status"),
    [
        pytest.param(date(2026, 1, 15), "6485.95", DueStatus.OK, id="both-clear"),
        pytest.param(date(2026, 5, 1), "6485.95", DueStatus.WARNING, id="calendar-warn-opens-30-days-before"),
        pytest.param(date(2026, 4, 30), "6485.95", DueStatus.OK, id="calendar-a-day-before-the-warning"),
        pytest.param(date(2026, 1, 15), "6607.7", DueStatus.WARNING, id="usage-warn-opens-50-before"),
        pytest.param(date(2026, 1, 15), "6657.7", DueStatus.DUE, id="usage-at-target"),
        pytest.param(date(2026, 1, 15), "6667.7", DueStatus.GRACE, id="usage-grace-ends-10-after"),
        pytest.param(date(2026, 1, 15), "6667.8", DueStatus.OVERDUE, id="usage-beyond-grace"),
        pytest.param(date(2026, 5, 31), "6485.95", DueStatus.DUE, id="calendar-due-all-day"),
        pytest.param(date(2026, 6, 1), "6485.95", DueStatus.OVERDUE, id="calendar-without-grace"),
    ],
)
def test_earliest_status_is_the_most_severe_term(today: date, reading: str, status: DueStatus) -> None:
    due = _MAINTAINED.resolve(anchors={"last_done": _LAST_DONE}, counters=_usage("6485.95", "2.5"))
    now = datetime.combine(today, datetime.min.time(), UTC) + timedelta(hours=12)

    assert due.status(now=now, counters=_usage(reading)) == status


def test_latest_status_is_the_least_severe_term() -> None:
    rule = _MAINTAINED.model_copy(update={"combine": "latest"})
    due = rule.resolve(anchors={"last_done": _LAST_DONE}, counters=_usage("6485.95"))

    assert due.status(now=_READ_AT, counters=_usage("6670")) == DueStatus.OK
    assert due.status(now=datetime(2026, 6, 2, tzinfo=UTC), counters=_usage("6670")) == DueStatus.OVERDUE


def test_status_needs_the_current_counter_reading() -> None:
    due = _MAINTAINED.resolve(anchors={"last_done": _LAST_DONE})

    with pytest.raises(ValidationError, match="usage_hours"):
        due.status(now=_READ_AT)


@pytest.mark.parametrize(
    ("now", "status"),
    [
        pytest.param(datetime(2026, 3, 10, 4, tzinfo=UTC), DueStatus.OK, id="before-warning"),
        pytest.param(datetime(2026, 3, 11, 4, tzinfo=UTC), DueStatus.WARNING, id="warning-opens-at-local-midnight"),
        pytest.param(datetime(2026, 3, 14, 3, 59, tzinfo=UTC), DueStatus.WARNING, id="the-evening-before"),
        pytest.param(datetime(2026, 3, 14, 4, tzinfo=UTC), DueStatus.DUE, id="the-due-day-opens"),
        pytest.param(datetime(2026, 3, 15, 3, 59, tzinfo=UTC), DueStatus.DUE, id="the-due-day-ends"),
        pytest.param(datetime(2026, 3, 16, 3, 59, tzinfo=UTC), DueStatus.GRACE, id="one-grace-day"),
        pytest.param(datetime(2026, 3, 16, 4, tzinfo=UTC), DueStatus.OVERDUE, id="after-grace"),
    ],
)
def test_a_due_day_is_read_on_its_own_local_calendar(now: datetime, status: DueStatus) -> None:
    rule = DueRule.of(Term(anchor="start_day", warn=Offset(3, "days"), grace=Offset(1, "days")))
    due = rule.resolve(anchors={"start_day": Anchor(on=date(2026, 3, 14), zone="America/Barbados")})

    assert due.status(now=now) == status


@pytest.mark.parametrize(
    ("now", "status"),
    [
        pytest.param(datetime(2026, 3, 14, 15, 59, tzinfo=UTC), DueStatus.OK, id="before-warning"),
        pytest.param(datetime(2026, 3, 14, 16, tzinfo=UTC), DueStatus.WARNING, id="warning-two-hours-before"),
        pytest.param(datetime(2026, 3, 14, 18, tzinfo=UTC), DueStatus.DUE, id="at-the-instant"),
        pytest.param(datetime(2026, 3, 14, 18, 30, tzinfo=UTC), DueStatus.GRACE, id="within-an-hour"),
        pytest.param(datetime(2026, 3, 14, 19, 0, 1, tzinfo=UTC), DueStatus.OVERDUE, id="after-the-hour"),
    ],
)
def test_an_instant_is_due_exactly_then(now: datetime, status: DueStatus) -> None:
    rule = DueRule.of(
        Term(anchor="start_day", align=[Align.at_time("14:00")], warn=Offset(2, "hours"), grace=Offset(1, "hours"))
    )
    due = rule.resolve(anchors={"start_day": Anchor(on=date(2026, 3, 14), zone="America/Barbados")})

    assert due.status(now=now) == status


def test_hour_windows_on_a_due_day_run_from_its_edges() -> None:
    """Warning opens 6 hours before the day starts; grace runs 6 hours past its end."""

    rule = DueRule.of(Term(anchor="start_day", warn=Offset(6, "hours"), grace=Offset(6, "hours")))
    due = rule.resolve(anchors={"start_day": Anchor(on=date(2026, 3, 14), zone="America/Barbados")})

    assert due.status(now=datetime(2026, 3, 13, 21, 59, tzinfo=UTC)) == DueStatus.OK
    assert due.status(now=datetime(2026, 3, 13, 22, tzinfo=UTC)) == DueStatus.WARNING
    assert due.status(now=datetime(2026, 3, 15, 9, 59, tzinfo=UTC)) == DueStatus.GRACE
    assert due.status(now=datetime(2026, 3, 15, 10, 0, 1, tzinfo=UTC)) == DueStatus.OVERDUE


# --- basis ---------------------------------------------------------------------------------------------------


def test_the_basis_is_stable_json_that_reads_back_with_the_same_status() -> None:
    due = DueRule.of(Term(anchor="first_slot", offset=Offset(-48, "hours"), grace=Offset(2, "hours"))).resolve(
        anchors={"first_slot": _at(BARBADOS, 2026, 3, 14, 19)}
    )

    assert canonical_json(due.basis) == (
        '{"at":"2026-03-12T23:00:00Z","local_date":"2026-03-12","terms":[{"at":"2026-03-12T23:00:00Z",'
        '"local_date":"2026-03-12","origin":{"at":"2026-03-14T23:00:00Z","zone":"America/Barbados"},'
        '"term":{"anchor":"first_slot","grace":{"amount":"2","unit":"hours"},"offset":{"amount":"-48",'
        '"unit":"hours"}},"zone":"America/Barbados"}],"winner":""}'
    )
    stored = json.loads(json.dumps(due.basis))
    read_back = Due.model_validate(stored)
    assert read_back == due
    now = datetime(2026, 3, 13, tzinfo=UTC)
    assert read_back.status(now=now) == due.status(now=now) == DueStatus.GRACE


def test_a_pending_basis_names_the_missing_anchor() -> None:
    due = DueRule.of(Term(anchor="first_slot", offset=Offset(-48, "hours"))).resolve(anchors={})

    assert due.basis == {
        "terms": [{"term": {"anchor": "first_slot", "offset": {"amount": "-48", "unit": "hours"}}}],
        "pending": ["first_slot"],
    }


# --- repeat --------------------------------------------------------------------------------------------------


def test_repeat_from_completion_counts_the_next_due_from_the_work() -> None:
    due = _MAINTAINED.resolve(anchors={"last_done": _LAST_DONE})
    done = Anchor.reading(on=date(2026, 3, 20), readings={"usage_hours": Decimal("6650")})

    following = _MAINTAINED.next(due, done=done)

    assert following is not None
    assert (following.winner, following.local_date) == ("calendar", date(2028, 3, 31))
    assert following.counter_targets == {"usage_hours": Decimal("6950")}


def test_a_rule_that_repeats_once_ends() -> None:
    rule = DueRule.of(Term(anchor="created", offset=Offset(7, "days")))
    due = rule.resolve(anchors={"created": date(2026, 1, 1)})

    assert rule.next(due, done=date(2026, 1, 5)) is None


@pytest.mark.parametrize(
    ("start", "following"),
    [
        pytest.param(Anchor(on=date(2026, 1, 5), zone="America/New_York"), (None, date(2026, 1, 12)), id="a-day"),
        pytest.param(
            _at(NEW_YORK, 2026, 3, 2, 9),
            (datetime(2026, 3, 9, 13, tzinfo=UTC), date(2026, 3, 9)),
            id="an-instant-keeps-its-local-clock-across-dst",
        ),
    ],
)
def test_a_calendar_repeat_steps_to_the_next_occurrence_after_the_due_point(
    start: Anchor, following: tuple[datetime | None, date]
) -> None:
    rule = DueRule.of(Term(anchor="start"), repeat=Repeat.calendar("FREQ=WEEKLY"))
    due = rule.resolve(anchors={"start": start})

    after = rule.next(due, done=date(2026, 6, 1))

    assert after is not None
    assert (after.at, after.local_date) == following
    assert after.previous == start.model_copy(update={"readings": {}})
    assert rule.next(after, done=date(2026, 6, 1)) is not None


def test_a_calendar_repeat_ends_with_its_rule() -> None:
    rule = DueRule.of(Term(anchor="start"), repeat=Repeat.calendar("FREQ=WEEKLY;UNTIL=20260112T235959Z"))
    due = rule.resolve(anchors={"start": Anchor(on=date(2026, 1, 5), zone="UTC")})

    after = rule.next(due, done=date(2026, 1, 5))
    assert after is not None and after.local_date == date(2026, 1, 12)
    assert rule.next(after, done=date(2026, 1, 12)) is None


# --- refusals ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda: Term(anchor="a", counter="c", offset=Offset(3, "days")), id="counter-with-calendar-unit"),
        pytest.param(lambda: Term(anchor="a", offset=Offset(3)), id="calendar-without-unit"),
        pytest.param(lambda: Term(anchor="a", counter="c", align=[Align.end_of("day")]), id="counter-alignment"),
        pytest.param(lambda: Term(anchor="a", warn=Offset(-1, "days")), id="negative-window"),
        pytest.param(lambda: Offset("1.5", "days"), id="fractional-calendar-amount"),
        pytest.param(lambda: Offset("NaN"), id="not-a-number"),
        pytest.param(lambda: Align(kind="at_time", period="day"), id="alignment-without-its-target"),
        pytest.param(lambda: Align(kind="end_of", period="month", zone="UTC"), id="zone-off-a-local-time"),
        pytest.param(lambda: Align.at_time("14:00", zone="Nowhere/Town"), id="unknown-zone"),
        pytest.param(lambda: Anchor(at=datetime(2026, 1, 1, tzinfo=UTC), on=date(2026, 1, 1)), id="two-moments"),
        pytest.param(lambda: Anchor(at=datetime(2026, 1, 1)), id="naive-anchor"),
        pytest.param(lambda: Counter(reading=Decimal(1), at=_READ_AT, rate_per_day=Decimal(0)), id="zero-rate"),
        pytest.param(lambda: DueRule.of(Term(anchor="a"), Term(anchor="b")), id="unlabelled-terms"),
        pytest.param(
            lambda: DueRule.of(Term(anchor="a"), repeat=Repeat.from_completion()), id="completion-anchor-unused"
        ),
        pytest.param(
            lambda: DueRule.of(
                Term(label="x", anchor="a"), Term(label="y", anchor="a"), repeat=Repeat.calendar("FREQ=DAILY")
            ),
            id="calendar-repeat-of-two-terms",
        ),
        pytest.param(lambda: Repeat.calendar("FREQ=DAILY;COUNT=3"), id="calendar-repeat-count"),
        pytest.param(lambda: Repeat.calendar("not a rule"), id="calendar-repeat-grammar"),
    ],
)
def test_a_rule_that_cannot_resolve_is_refused_when_built(build) -> None:
    with pytest.raises(ValueError):
        build()


def test_resolve_and_status_refuse_naive_moments_and_missing_readings() -> None:
    with pytest.raises(ValidationError, match="anchors.created"):
        DueRule.of(Term(anchor="created")).resolve(anchors={"created": datetime(2026, 1, 1)})
    with pytest.raises(ValidationError, match="usage_hours"):
        _MAINTAINED.resolve(anchors={"last_done": date(2024, 5, 2)})
    due = DueRule.of(Term(anchor="created")).resolve(anchors={"created": date(2026, 1, 1)})
    with pytest.raises(ValidationError):
        due.status(now=datetime(2026, 1, 1))


# --- the field -----------------------------------------------------------------------------------------------


def test_the_field_stores_the_canonical_rule_and_blank_as_null() -> None:
    field = _Item._meta.get_field("due_rule")
    authored = {"terms": [{"anchor": "created", "offset": {"amount": 7, "unit": "days"}}], "combine": "earliest"}

    assert field.clean(authored, None) == {"terms": [{"anchor": "created", "offset": {"amount": "7", "unit": "days"}}]}
    assert field.clean(None, None) is None
    item = _Item(due_rule=DueRule.of(Term(anchor="created")))
    assert field.pre_save(item, add=True) == {"terms": [{"anchor": "created"}]}
    assert item.due_rule == {"terms": [{"anchor": "created"}]}
    assert field.get_prep_value(DueRule.of(Term(anchor="created"))) == {"terms": [{"anchor": "created"}]}


def test_the_field_refuses_an_invalid_rule_naming_the_path_inside_it() -> None:
    item = _Item(due_rule={"terms": [{"anchor": "created", "offset": {"amount": 7, "unit": "fortnights"}}]})

    with pytest.raises(ValidationError) as error:
        item.full_clean(exclude=["due_date", "due_basis"])
    [message] = error.value.message_dict["due_rule"]
    assert message.startswith("terms.0.offset.unit:")


def test_the_field_round_trips_through_a_model_form_as_json() -> None:
    form_class = modelform_factory(_Item, fields=["due_rule"])
    rule = {"terms": [{"anchor": "created", "offset": {"amount": 7, "unit": "days"}}]}

    form = form_class(data={"due_rule": json.dumps(rule)})
    assert form.is_valid(), form.errors
    assert form.instance.due_rule == {"terms": [{"anchor": "created", "offset": {"amount": "7", "unit": "days"}}]}
    assert json.loads(form_class(instance=form.instance)["due_rule"].value()) == form.instance.due_rule

    refused = form_class(data={"due_rule": json.dumps({"terms": []})})
    assert not refused.is_valid()
    [refusal] = refused.errors["due_rule"]
    assert refusal.endswith("A rule has at least one term.")


def test_the_field_round_trips_through_graphql_as_json() -> None:
    schema = strawberry.Schema(query=_ItemQuery, mutation=_ItemMutation)
    printed = str(schema)
    assert "dueRule: JSON" in printed

    read = schema.execute_sync("{ item { dueRule } }")
    assert read.errors is None
    assert read.data == {
        "item": {"dueRule": {"terms": [{"anchor": "created", "offset": {"amount": "7", "unit": "days"}}]}}
    }

    written = schema.execute_sync(
        "mutation($rule: JSON) { checkItem(data: {dueRule: $rule}) { dueRule } }",
        variable_values={"rule": {"terms": [{"anchor": "created", "align": [{"kind": "end_of", "period": "week"}]}]}},
    )
    assert written.errors is None
    assert written.data == {
        "checkItem": {"dueRule": {"terms": [{"anchor": "created", "align": [{"kind": "end_of", "period": "week"}]}]}}
    }


def test_the_field_renders_the_json_widget_and_migrates_cleanly() -> None:
    field = _Item._meta.get_field("due_rule")
    assert resource_field_widget(field, "scalar", scalar="JSON") == "json"

    state = ModelState.from_model(_Item)
    migration = migrations.Migration("0001_initial", "scheduling")
    migration.operations = [migrations.CreateModel(state.name, list(state.fields.items()))]
    assert "angee.scheduling.fields.DueRuleField(" in MigrationWriter(migration).as_string()


# --- properties ----------------------------------------------------------------------------------------------

_INSTANTS = st.datetimes(
    min_value=datetime(2000, 1, 1), max_value=datetime(2040, 12, 31), timezones=st.just(NEW_YORK), allow_imaginary=False
)
_DAYS = st.dates(min_value=date(2000, 1, 1), max_value=date(2040, 12, 31))


@settings(deadline=None, max_examples=60)
@given(start=_INSTANTS, hours=st.integers(-2000, 2000))
@example(start=datetime(2026, 3, 9, 19, tzinfo=NEW_YORK), hours=-48)
def test_elapsed_offsets_are_exact_on_the_utc_timeline(start: datetime, hours: int) -> None:
    rule = DueRule.of(Term(anchor="a", offset=Offset(hours, "hours")))
    due = rule.resolve(anchors={"a": Anchor(at=start, zone="America/New_York")})

    assert due.at is not None
    assert due.at - start == timedelta(hours=hours)
    assert due.local_date == due.at.astimezone(NEW_YORK).date()


@settings(deadline=None, max_examples=60)
@given(day=_DAYS, weekday=st.sampled_from(WEEKDAYS), strict=st.booleans())
def test_the_next_weekday_is_that_weekday_within_a_week(day: date, weekday: str, strict: bool) -> None:
    rule = DueRule.of(Term(anchor="a", align=[Align.next_weekday(weekday, strict=strict)]))
    result = rule.resolve(anchors={"a": day}).local_date

    assert result is not None
    assert result.weekday() == WEEKDAYS.index(weekday)
    assert (1 if strict else 0) <= (result - day).days <= (7 if strict else 6)


@settings(deadline=None, max_examples=60)
@given(
    day=_DAYS,
    warn=st.integers(0, 10),
    grace=st.integers(0, 10),
    moments=st.lists(st.integers(-20 * 24, 20 * 24), min_size=2, max_size=12),
)
def test_status_never_eases_as_time_passes(day: date, warn: int, grace: int, moments: list[int]) -> None:
    rule = DueRule.of(Term(anchor="a", warn=Offset(warn, "days"), grace=Offset(grace, "days")))
    due = rule.resolve(anchors={"a": Anchor(on=day, zone="America/New_York")})
    midnight = datetime.combine(day, datetime.min.time(), NEW_YORK)
    severities = [
        tuple(DueStatus).index(due.status(now=midnight + timedelta(hours=hours))) for hours in sorted(moments)
    ]

    assert severities == sorted(severities)


@settings(deadline=None, max_examples=60)
@given(day=_DAYS, first=st.integers(-400, 400), second=st.integers(-400, 400), at_hour=st.integers(0, 23))
def test_earliest_never_falls_after_latest(day: date, first: int, second: int, at_hour: int) -> None:
    terms = (
        Term(label="days", anchor="a", offset=Offset(first, "days")),
        Term(label="clock", anchor="a", offset=Offset(second, "hours"), align=[Align.at_time(f"{at_hour:02d}:00")]),
    )
    anchors = {"a": Anchor(on=day, zone="America/Barbados")}
    earliest = DueRule.of(*terms).resolve(anchors=anchors)
    latest = DueRule.of(*terms, combine="latest").resolve(anchors=anchors)

    winners = {due.winner: due for due in (earliest, latest)}
    assert set(winners) <= {"days", "clock"}
    first_deadline = next(term for term in earliest.terms if term.term.label == earliest.winner).deadline()
    last_deadline = next(term for term in latest.terms if term.term.label == latest.winner).deadline()
    assert first_deadline is not None and last_deadline is not None
    assert first_deadline <= last_deadline


@settings(deadline=None, max_examples=60)
@given(
    start=_INSTANTS,
    amount=st.integers(-100, 100),
    unit=st.sampled_from(["minutes", "hours", "days", "weeks", "months", "years"]),
    align=st.lists(
        st.one_of(
            st.sampled_from(["day", "week", "month", "quarter", "year"]).map(Align.end_of),
            st.sampled_from(WEEKDAYS).map(lambda weekday: Align.next_weekday(weekday, strict=True)),
            st.integers(0, 23).map(lambda hour: Align.at_time(f"{hour:02d}:30")),
        ),
        max_size=3,
    ),
)
def test_a_basis_reads_back_to_the_same_resolution(start: datetime, amount: int, unit: str, align: list[Align]) -> None:
    rule = DueRule.of(Term(anchor="a", offset=Offset(amount, unit), align=align))
    due = rule.resolve(anchors={"a": Anchor(at=start, zone="America/New_York")})
    stored = json.loads(canonical_json(due.basis))

    assert Due.model_validate(stored) == due
    assert Due.model_validate(stored).basis == due.basis
    assert DueRule.model_validate(json.loads(canonical_json(rule.as_json()))) == rule
