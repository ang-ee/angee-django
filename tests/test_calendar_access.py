"""Who may do what with a calendar: the decision table, then each enforcement point.

``calendar/calendar`` is the access root. Its holder (the user of the person whose
calendar it is), its creator and its ``editor`` grantees write it; ``reader``
grantees read it; ``free_busy`` grantees see its busy time only. Event details
follow the event's visibility. The company arm is a consumer's and is proved on a
host that composes it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from rebac import PermissionDenied, actor_context, system_context
from rebac.testing import assert_decisions

from angee.calendar import schema as calendar_schema
from angee.calendar.models import EventTransparency, EventVisibility
from angee.calendar.testing.models import Calendar, CalendarEvent, EventAttendee
from angee.graphql.testing.documents import execute_schema, result_data
from angee.messaging.testing.models import Handle, Organization, Person
from tests.conftest import addon_schema, create_platform_admin, create_user
from tests.decision_table import seat_cells

SEATS = ("holder", "reader", "editor", "viewer", "stranger", "admin")


def _utc(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, day, hour, minute, tzinfo=UTC)


@pytest.fixture
def world(db: None) -> SimpleNamespace:
    """The holder's calendar shared as reader, editor and free/busy, with one event per visibility."""

    del db
    with system_context(reason="tests.calendar.access.setup"):
        actors = {seat: create_user(f"cal_{seat}") for seat in SEATS if seat != "admin"}
        person = Person.objects.for_user(actors["holder"])
        calendar = Calendar.objects.create(party=person, name="Holder")
        events = {
            visibility.value: CalendarEvent.objects.create(
                calendar=calendar, title=f"{visibility.value} meeting", visibility=visibility,
                start=_utc(13, hour), end=_utc(13, hour, 30),
            )
            for visibility, hour in (
                (EventVisibility.DEFAULT, 9), (EventVisibility.PRIVATE, 11), (EventVisibility.PUBLIC, 14),
            )
        }
        free = CalendarEvent.objects.create(
            calendar=calendar, title="Lunch", transparency=EventTransparency.FREE, start=_utc(13, 12), end=_utc(13, 13),
        )
        attendee = EventAttendee.objects.create(
            event=events["default"], handle=Handle.objects.create(platform="email", value="guest@example.test"),
        )
    actors["admin"] = create_platform_admin("cal_admin")
    shared = _as(actors["holder"], calendar)
    for relation, seat in (("reader", "reader"), ("editor", "editor"), ("free_busy", "viewer")):
        shared.grant_record_access(relation, actors[seat])
    return SimpleNamespace(calendar=calendar, free=free, attendee=attendee, **actors, **events)


def _as(actor: Any, calendar: Any) -> Any:
    """Load ``calendar`` as ``actor`` reads it, so its verbs check that actor."""

    with actor_context(actor):
        return Calendar.objects.with_actor(actor).get(pk=calendar.pk)


def test_the_calendar_decision_table(world: SimpleNamespace) -> None:
    w = world
    actor = lambda seat: getattr(w, seat)  # noqa: E731
    rows = [
        (w.calendar, "read", "holder reader editor admin", "viewer stranger"),
        (w.calendar, "see_busy", "holder reader editor viewer admin", "stranger"),
        (w.calendar, "write", "holder editor admin", "reader viewer stranger"),
        (w.calendar, "share", "holder editor admin", "reader viewer stranger"),
        (w.calendar, "delete", "holder admin", "reader editor viewer stranger"),
        (w.default, "read", "holder reader editor admin", "viewer stranger"),
        (w.private, "read", "holder editor admin", "reader viewer stranger"),
        (w.public, "read", "holder reader editor viewer admin", "stranger"),
        (w.default, "see_busy", "holder reader editor viewer admin", "stranger"),
        (w.default, "write", "holder editor admin", "reader viewer stranger"),
        (w.attendee, "read", "holder reader editor admin", "viewer stranger"),
        (w.attendee, "write", "holder editor admin", "reader viewer stranger"),
    ]

    assert_decisions(
        checks=seat_cells(actor, *rows),
        accessible=[
            (w.reader, "read", CalendarEvent, {w.default, w.public, w.free}),
            (w.viewer, "read", CalendarEvent, {w.public}),
            (w.viewer, "see_busy", CalendarEvent, {w.default, w.private, w.public, w.free}),
            (w.stranger, "see_busy", CalendarEvent, set()),
        ],
    )


def test_a_free_busy_viewer_reads_no_event_row_and_sees_redacted_busy_time(world: SimpleNamespace) -> None:
    w = world

    rows = list(CalendarEvent.objects.with_actor(w.viewer).filter(calendar=w.calendar))
    occurrences = CalendarEvent.objects.with_actor(w.viewer).occurrences(_utc(13, 0), _utc(14, 0))

    assert rows == [w.public]
    # Default and private events are busy time; the public one reads in full; free time never shows.
    assert [(o.event_sqid, o.title, o.redacted, o.editable) for o in occurrences] == [
        (w.default.sqid, "", True, False),
        (w.private.sqid, "", True, False),
        (w.public.sqid, "public meeting", False, True),
    ]
    assert CalendarEvent.objects.with_actor(w.stranger).occurrences(_utc(13, 0), _utc(14, 0)) == []


def test_a_creator_keeps_the_calendar_of_a_party_without_a_user(db: None) -> None:
    del db
    with system_context(reason="tests.calendar.access.setup"):
        founder, outsider = create_user("cal_founder"), create_user("cal_outsider")
    with actor_context(founder):
        organization = Organization.objects.create(display_name="Studio")
        calendar = Calendar.objects.create(party=organization, name="Studio")

    assert list(Calendar.objects.with_actor(founder).filter(pk=calendar.pk)) == [calendar]
    assert list(Calendar.objects.with_actor(outsider).filter(pk=calendar.pk)) == []
    # Only whoever may edit the party gives it a calendar.
    with actor_context(outsider), pytest.raises(PermissionDenied):
        Calendar.objects.create(party=organization, name="Not mine")


_OCCURRENCES = """
query ($start: DateTime!, $end: DateTime!, $calendars: [ID!]) {
  event_occurrences(window_start: $start, window_end: $end, calendars: $calendars) {
    event_sqid calendar title redacted editable
  }
}
"""

_RESCHEDULE = """
mutation ($id: ID!, $start: DateTime!, $end: DateTime!) {
  reschedule_event(id: $id, start: $start, end: $end) { ok message }
}
"""


def _console() -> Any:
    return addon_schema(calendar_schema.schemas, "console")


def test_event_occurrences_redacts_for_a_free_busy_viewer_and_narrows_by_calendar(world: SimpleNamespace) -> None:
    w = world
    window = {"start": _utc(13, 0).isoformat(), "end": _utc(14, 0).isoformat()}

    viewed = result_data(execute_schema(_console(), _OCCURRENCES, window, user=w.viewer))["event_occurrences"]
    elsewhere = result_data(
        execute_schema(_console(), _OCCURRENCES, {**window, "calendars": ["cal_unknown"]}, user=w.holder)
    )["event_occurrences"]

    assert [(row["title"], row["redacted"], row["calendar"]) for row in viewed] == [
        ("", True, w.calendar.sqid),
        ("", True, w.calendar.sqid),
        ("public meeting", False, w.calendar.sqid),
    ]
    assert elsewhere == []


@pytest.mark.parametrize(
    ("seat", "recurrence", "moved"),
    [
        pytest.param("editor", "", True, id="a-writer-drags-a-single-event"),
        pytest.param("reader", "", False, id="a-reader-may-not"),
        pytest.param("holder", "FREQ=WEEKLY", False, id="a-recurring-master-is-not-dragged"),
    ],
)
def test_reschedule_event_moves_only_what_the_actor_may_write(
    world: SimpleNamespace, seat: str, recurrence: str, moved: bool,
) -> None:
    w = world
    with system_context(reason="tests.calendar.access.setup"):
        event = CalendarEvent.objects.create(
            calendar=w.calendar, title="Sync", recurrence=recurrence, start=_utc(20, 9), end=_utc(20, 10),
        )
    variables = {"id": event.sqid, "start": _utc(21, 9).isoformat(), "end": _utc(21, 10).isoformat()}

    result = result_data(execute_schema(_console(), _RESCHEDULE, variables, user=getattr(w, seat)))["reschedule_event"]
    with system_context(reason="tests.calendar.access.read"):
        event.refresh_from_db()

    assert result["ok"] is moved
    assert event.start == (_utc(21, 9) if moved else _utc(20, 9))


def test_sharing_is_managed_by_writers_only(world: SimpleNamespace) -> None:
    w = world

    _as(w.editor, w.calendar).grant_record_access("reader", w.stranger)
    with pytest.raises(PermissionDenied):
        _as(w.reader, w.calendar).grant_record_access("editor", w.reader)

    assert list(Calendar.objects.with_actor(w.stranger).filter(pk=w.calendar.pk)) == [w.calendar]
