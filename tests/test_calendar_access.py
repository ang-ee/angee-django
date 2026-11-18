"""Who may do what with a calendar: the decision table, then each enforcement point.

``calendar/calendar`` is the access root. Its holder (the user of the person whose
calendar it is), its creator and its ``editor`` grantees write it; ``reader``
grantees read it; ``free_busy`` grantees see its busy time only. Event details
follow the event's visibility. The company arm is a consumer's and is proved on a
host that composes it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rebac import PermissionDenied, actor_context, system_context, to_subject_ref
from rebac.testing import assert_decisions

from angee.calendar import schema as calendar_schema
from angee.calendar.models import OVERLAP_MESSAGE, EventTransparency, EventVisibility
from angee.calendar.schema import FreeBusyChangeReadGate
from angee.calendar.testing.models import Calendar, CalendarEvent, EventAttendee
from angee.graphql.events import ChangePayload
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
        # A calendar created as system work has no owner: its holder alone holds it.
        held = Calendar.objects.create(party=person, name="Held")
    with actor_context(actors["holder"]):
        calendar = Calendar.objects.create(party=person, name="Holder")
    with system_context(reason="tests.calendar.access.setup"):
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
    return SimpleNamespace(calendar=calendar, held=held, free=free, attendee=attendee, **actors, **events)


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
        # The holder created this calendar, so it is also its owner.
        (w.calendar, "share", "holder editor admin", "reader viewer stranger"),
        (w.calendar, "delete", "holder admin", "reader editor viewer stranger"),
        (w.calendar, "transfer", "holder admin", "reader editor viewer stranger"),
        # An ownerless calendar (system work created it): its holder still writes, deletes and shares it.
        (w.held, "write", "holder admin", "reader editor viewer stranger"),
        (w.held, "share", "holder admin", "reader editor viewer stranger"),
        (w.held, "transfer", "admin", "holder reader editor viewer stranger"),
        (w.default, "read", "holder reader editor admin", "viewer stranger"),
        (w.private, "read", "holder editor admin", "reader viewer stranger"),
        (w.public, "read", "holder reader editor viewer admin", "stranger"),
        (w.default, "see_busy", "holder reader editor viewer admin", "stranger"),
        (w.default, "write", "holder editor admin", "reader viewer stranger"),
        (w.attendee, "read", "holder reader editor admin", "viewer stranger"),
        (w.attendee, "write", "holder editor admin", "reader viewer stranger"),
        # An event's readers read its attendees' addresses; nobody else was granted this one.
        (w.attendee.handle, "read", "holder reader editor admin", "viewer stranger"),
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
        (w.public.sqid, "public meeting", False, False),
    ]
    assert CalendarEvent.objects.with_actor(w.stranger).occurrences(_utc(13, 0), _utc(14, 0)) == []


def test_the_creator_owns_a_calendar_and_ownership_transfers(db: None) -> None:
    del db
    with system_context(reason="tests.calendar.access.setup"):
        founder, successor, outsider = (create_user(f"cal_{name}") for name in ("founder", "successor", "outsider"))
    with actor_context(founder):
        organization = Organization.objects.create(display_name="Studio")
        calendar = Calendar.objects.create(party=organization, name="Studio")

    assert calendar.owner_id == founder.pk
    assert list(Calendar.objects.with_actor(founder).filter(pk=calendar.pk)) == [calendar]
    assert list(Calendar.objects.with_actor(outsider).filter(pk=calendar.pk)) == []

    with actor_context(founder):
        Calendar.objects.with_actor(founder).get(pk=calendar.pk).transfer_ownership(successor)

    assert_decisions(
        checks=[
            (successor, "share", calendar, True),
            (successor, "transfer", calendar, True),
            (founder, "read", calendar, False),
        ],
    )
    # Only whoever may edit the party gives it a calendar.
    with actor_context(outsider), pytest.raises(PermissionDenied):
        Calendar.objects.create(party=organization, name="Not mine")


_OCCURRENCES = """
query ($start: DateTime!, $end: DateTime!) {
  event_occurrences(window_start: $start, window_end: $end) {
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


def test_event_occurrences_redacts_for_a_free_busy_viewer_and_drags_only_for_writers(world: SimpleNamespace) -> None:
    w = world
    window = {"start": _utc(13, 0).isoformat(), "end": _utc(14, 0).isoformat()}

    def read(seat: str) -> list[dict[str, Any]]:
        return result_data(execute_schema(_console(), _OCCURRENCES, window, user=getattr(w, seat)))["event_occurrences"]

    assert [(row["title"], row["redacted"], row["calendar"], row["editable"]) for row in read("viewer")] == [
        ("", True, w.calendar.sqid, False),
        ("", True, w.calendar.sqid, False),
        ("public meeting", False, w.calendar.sqid, False),
    ]
    # Readers see the default, free and public events in full and drag none; writers drag them all.
    assert {row["editable"] for row in read("reader")} == {False}
    assert {row["editable"] for row in read("editor")} == {True}


def test_free_busy_viewers_learn_only_that_busy_time_changed(world: SimpleNamespace) -> None:
    w = world
    with system_context(reason="tests.calendar.access.change"):
        payload = ChangePayload.from_instance(w.default, action="update", update_fields=("title",))

    def delivered(seat: str) -> Any:
        return FreeBusyChangeReadGate(CalendarEvent, to_subject_ref(getattr(w, seat))).filter(payload)

    assert delivered("reader").changed_fields == ["title"]
    bare = delivered("viewer")
    assert (bare.id, bare.changed_fields, bare.changed_values) == (w.default.sqid, None, None)
    assert delivered("stranger") is None
    # A free event is never busy time: free/busy viewers never hear of it.
    with system_context(reason="tests.calendar.access.change"):
        payload = ChangePayload.from_instance(w.free, action="update", update_fields=("title",))
    assert delivered("viewer") is None
    assert delivered("reader") is not None
    # Turning an event free takes a busy block away, so that change still reaches them.
    with system_context(reason="tests.calendar.access.change"):
        payload = ChangePayload.from_instance(w.free, action="update", update_fields=("transparency",))
    assert delivered("viewer").changed_fields is None


_INSERT_OVERRIDE = """
mutation ($calendar: ID!, $series: ID!, $original: DateTime!, $start: DateTime!, $end: DateTime!) {
  insert_calendar_events_one(object: {
    calendar: $calendar, series: $series, original_start: $original, title: "Moved", start: $start, end: $end
  }) { id visibility transparency }
}
"""


def test_an_override_inserted_without_visibility_or_transparency_takes_its_masters(world: SimpleNamespace) -> None:
    w = world
    with system_context(reason="tests.calendar.access.setup"):
        master = CalendarEvent.objects.create(
            calendar=w.calendar, title="Weekly", recurrence="FREQ=WEEKLY", visibility=EventVisibility.PRIVATE,
            transparency=EventTransparency.FREE, start=_utc(20, 9), end=_utc(20, 10),
        )
    variables = {
        "calendar": w.calendar.sqid, "series": master.sqid, "original": _utc(27, 9).isoformat(),
        "start": _utc(27, 10).isoformat(), "end": _utc(27, 11).isoformat(),
    }

    inserted = result_data(execute_schema(_console(), _INSERT_OVERRIDE, variables, user=w.holder))
    assert inserted["insert_calendar_events_one"]["visibility"] == "PRIVATE"
    assert inserted["insert_calendar_events_one"]["transparency"] == "FREE"


_INSERT = """
mutation ($calendar: ID!, $start: DateTime!, $end: DateTime!) {
  insert_calendar_events_one(object: {calendar: $calendar, title: "Mine", start: $start, end: $end}) { id }
}
"""


def test_a_refused_insert_on_a_refuse_calendar_learns_nothing_of_its_events(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reader may not insert; the refusal names no event and does not say the time is taken."""

    w = world
    # The overlap check judges from now: before this July's events.
    monkeypatch.setattr(timezone, "now", lambda: datetime(2026, 7, 1, tzinfo=UTC))
    with system_context(reason="tests.calendar.access.setup"):
        w.calendar.overlap_policy = "refuse"
        w.calendar.save(update_fields=["overlap_policy", "updated_at"])
    variables = {
        "calendar": w.calendar.sqid, "start": _utc(13, 11, 10).isoformat(), "end": _utc(13, 11, 20).isoformat(),
    }

    refused = execute_schema(_console(), _INSERT, variables, user=w.reader)
    taken = execute_schema(_console(), _INSERT, variables, user=w.editor)

    told = repr(refused.errors) + repr(refused.data)
    assert refused.errors and "private meeting" not in told and OVERLAP_MESSAGE not in told
    # A writer is told the time is taken, still without the other event's title.
    assert taken.errors and OVERLAP_MESSAGE in repr(taken.errors) and "private meeting" not in repr(taken.errors)


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


# --- starting an event: which calendar --------------------------------------------

_WRITABLE = """
query { calendars(where: {writable: {_eq: true}}, order_by: [{name: asc}]) { id } }
"""

_ENSURE_PERSONAL = "mutation { ensure_personal_calendar { ok message id } }"

_INSERT_WITHOUT_CALENDAR = """
mutation ($start: DateTime!, $end: DateTime!) {
  insert_calendar_events_one(object: {title: "Unplaced", start: $start, end: $end}) { id }
}
"""


def _writable(actor: Any) -> list[str]:
    rows = result_data(execute_schema(_console(), _WRITABLE, {}, user=actor))["calendars"]
    return [row["id"] for row in rows]


def _insert_without_calendar(actor: Any) -> Any:
    variables = {"start": _utc(22, 9).isoformat(), "end": _utc(22, 10).isoformat()}
    return execute_schema(_console(), _INSERT_WITHOUT_CALENDAR, variables, user=actor)


@pytest.mark.parametrize(
    ("seat", "offered"),
    [
        pytest.param("stranger", (), id="none"),
        pytest.param("reader", (), id="a-reader-is-offered-none"),
        pytest.param("editor", ("calendar",), id="one"),
        pytest.param("holder", ("held", "calendar"), id="two"),
    ],
)
def test_the_event_form_offers_the_calendars_the_actor_may_write(
    world: SimpleNamespace, seat: str, offered: tuple[str, ...],
) -> None:
    """The picker lists writable calendars; the form defaults only when there is exactly one.

    Whatever is offered, an insert without a calendar is refused naming its
    ``calendar`` input: the field is required (``ID!``), which is also what makes
    the form mark it required before it sends anything.
    """

    w = world
    actor = getattr(w, seat)

    assert _writable(actor) == [getattr(w, name).sqid for name in offered]
    refused = _insert_without_calendar(actor)
    assert refused.errors and "required field 'calendar'" in refused.errors[0].message


def test_a_user_without_a_calendar_starts_their_own_once(world: SimpleNamespace) -> None:
    """The verb creates one personal calendar, the user's person's and owned by the user, and then returns it."""

    w = world
    assert _writable(w.stranger) == []

    first = result_data(execute_schema(_console(), _ENSURE_PERSONAL, {}, user=w.stranger))["ensure_personal_calendar"]
    again = result_data(execute_schema(_console(), _ENSURE_PERSONAL, {}, user=w.stranger))["ensure_personal_calendar"]

    assert first["ok"] is True and again["id"] == first["id"]
    assert _writable(w.stranger) == [first["id"]]
    with system_context(reason="tests.calendar.access.read"):
        created = Calendar.objects.get(sqid=first["id"])
        assert Calendar.objects.filter(owner=w.stranger).count() == 1
        assert (created.owner_id, created.party_id) == (w.stranger.pk, Person.objects.get(user=w.stranger).pk)


# --- deleting: the authored delete previews the UI's Delete composes -------------

_DELETE_EVENT = """
mutation ($id: ID!, $confirm: Boolean! = false) {
  delete_calendar_event(id: $id, confirm: $confirm) { has_blockers refusals deleted { label count } }
}
"""

_DELETE_CALENDAR = """
mutation ($id: ID!, $confirm: Boolean! = false) {
  delete_calendar(id: $id, confirm: $confirm) { has_blockers refusals deleted { label count } }
}
"""


def test_calendars_and_events_declare_the_delete_preview_that_offers_delete() -> None:
    resources = {resource.model_label: resource for resource in _console().angee_resources}

    for label, root in (("calendar.Calendar", "delete_calendar"), ("calendar.CalendarEvent", "delete_calendar_event")):
        assert "deletePreview" in resources[label].capabilities
        assert resources[label].as_wire(schema_name="console")["roots"]["deletePreview"] == root


def test_an_events_writer_previews_then_deletes_it_and_a_reader_may_not(world: SimpleNamespace) -> None:
    w = world
    event = {"id": w.default.sqid}

    refused = execute_schema(_console(), _DELETE_EVENT, {**event, "confirm": True}, user=w.reader)
    preview = result_data(execute_schema(_console(), _DELETE_EVENT, event, user=w.editor))["delete_calendar_event"]
    with system_context(reason="tests.calendar.access.read"):
        assert CalendarEvent.objects.filter(pk=w.default.pk).exists()
    confirmed = execute_schema(_console(), _DELETE_EVENT, {**event, "confirm": True}, user=w.editor)

    assert refused.errors
    # The event goes with its attendee.
    assert (preview["has_blockers"], preview["refusals"]) == (False, [])
    assert {group["label"]: group["count"] for group in preview["deleted"]} == {
        "calendar events": 1, "event attendees": 1,
    }
    assert result_data(confirmed)["delete_calendar_event"]["has_blockers"] is False
    with system_context(reason="tests.calendar.access.read"):
        assert not CalendarEvent.objects.filter(pk=w.default.pk).exists()


def test_a_calendars_holder_previews_its_events_going_with_it_and_an_editor_may_not_delete_it(
    world: SimpleNamespace,
) -> None:
    w = world
    calendar = {"id": w.calendar.sqid}

    preview = result_data(execute_schema(_console(), _DELETE_CALENDAR, calendar, user=w.holder))["delete_calendar"]
    refused = execute_schema(_console(), _DELETE_CALENDAR, {**calendar, "confirm": True}, user=w.editor)

    assert (preview["has_blockers"], preview["refusals"]) == (False, [])
    assert {group["label"]: group["count"] for group in preview["deleted"]} == {
        "calendars": 1, "calendar events": 4, "event attendees": 1,
    }
    assert refused.errors
    with system_context(reason="tests.calendar.access.read"):
        assert Calendar.objects.filter(pk=w.calendar.pk).exists()


_INSERT_ALL_DAY = """
mutation ($calendar: ID!, $start: DateTime!, $end: DateTime!) {
  insert_calendar_events_one(object: {calendar: $calendar, title: "Holiday", all_day: true, start: $start, end: $end}) {
    id start end all_day
  }
}
"""


@pytest.mark.parametrize(("zone", "midnight"), [
    # A blank zone is the project's (America/Chicago): the server places the day where the client cannot.
    ("", datetime(2026, 11, 20, 6, tzinfo=UTC)),
    ("Asia/Tokyo", datetime(2026, 11, 19, 15, tzinfo=UTC)),
])
def test_an_all_day_event_written_as_dates_lands_on_midnight_in_its_zone(
    world: SimpleNamespace, zone: str, midnight: datetime
) -> None:
    """A browser in any zone names the days; the event places them at midnight in its calendar's zone."""

    w = world
    with system_context(reason="tests.calendar.access.setup"):
        w.calendar.timezone = zone
        w.calendar.save(update_fields=["timezone", "updated_at"])
    variables = {"calendar": w.calendar.sqid, "start": "2026-11-20", "end": "2026-11-21"}

    inserted = result_data(execute_schema(_console(), _INSERT_ALL_DAY, variables, user=w.holder))

    with system_context(reason="tests.calendar.access.read"):
        event = CalendarEvent.objects.get(sqid=inserted["insert_calendar_events_one"]["id"])
    assert (event.all_day, event.start, event.end) == (True, midnight, midnight + timedelta(days=1))


def test_an_all_day_instant_off_its_zones_midnight_is_still_refused(world: SimpleNamespace) -> None:
    """An offset makes a bound an instant: Tokyo's midnight is not the project zone's, so it is refused, not moved."""

    w = world
    variables = {"calendar": w.calendar.sqid, "start": "2026-11-19T15:00:00Z", "end": "2026-11-20T15:00:00Z"}

    refused = execute_schema(_console(), _INSERT_ALL_DAY, variables, user=w.holder)

    assert refused.errors and "An all-day event starts at midnight in its time zone" in repr(refused.errors)


_EFFECTIVE_TIMEZONE = """
query ($id: String!, $start: DateTime!, $end: DateTime!) {
  calendar_events_by_pk(id: $id) { effective_timezone }
  event_occurrences(window_start: $start, window_end: $end) { event_sqid effective_timezone }
}
"""


@pytest.mark.parametrize(("event_zone", "calendar_zone", "expected"), [
    pytest.param("Asia/Tokyo", "Europe/Berlin", "Asia/Tokyo", id="its-own"),
    pytest.param("", "Europe/Berlin", "Europe/Berlin", id="its-calendars"),
    # The project zone (America/Chicago), which no client surface could otherwise read.
    pytest.param("", "", "America/Chicago", id="the-projects"),
])
def test_an_event_and_its_occurrences_read_its_effective_timezone(
    world: SimpleNamespace, event_zone: str, calendar_zone: str, expected: str,
) -> None:
    """A client reads an all-day event's days in this zone: the event's own, else its calendar's, else the project's."""

    w = world
    with system_context(reason="tests.calendar.access.setup"):
        w.calendar.timezone = calendar_zone
        w.calendar.save(update_fields=["timezone", "updated_at"])
        event = CalendarEvent.objects.create(
            calendar=w.calendar, title="Offsite", timezone=event_zone, start=_utc(22, 9), end=_utc(22, 10),
        )
    variables = {"id": event.sqid, "start": _utc(22, 0).isoformat(), "end": _utc(23, 0).isoformat()}

    read = result_data(execute_schema(_console(), _EFFECTIVE_TIMEZONE, variables, user=w.reader))

    assert read["calendar_events_by_pk"] == {"effective_timezone": expected}
    assert read["event_occurrences"] == [{"event_sqid": event.sqid, "effective_timezone": expected}]


def test_a_list_reads_each_events_effective_timezone_in_its_own_query(world: SimpleNamespace) -> None:
    """The field annotates the list's query: ten events on two calendars cost what four on one do, and no more."""

    w = world

    def read(fields: str) -> tuple[int, list[dict[str, Any]]]:
        with CaptureQueriesContext(connection) as captured:
            result = execute_schema(_console(), f"query {{ calendar_events {{ {fields} }} }}", {}, user=w.holder)
        return len(captured), result_data(result)["calendar_events"]

    plain, _rows = read("title")
    four, rows = read("title effective_timezone")
    assert {row["effective_timezone"] for row in rows} == {"America/Chicago"}
    with system_context(reason="tests.calendar.access.setup"):
        tokyo = Calendar.objects.create(party=w.calendar.party, name="Tokyo", timezone="Asia/Tokyo")
        for index in range(6):
            CalendarEvent.objects.create(
                calendar=tokyo if index % 2 else w.calendar, title=f"Extra {index}",
                timezone="Europe/Berlin" if index == 0 else "", start=_utc(20, 9 + index), end=_utc(20, 10 + index),
            )
    ten, rows = read("title effective_timezone")

    assert len(rows) == 10
    assert four == ten == plain
    assert sorted(row["effective_timezone"] for row in rows) == sorted(
        ["America/Chicago"] * 6 + ["Asia/Tokyo"] * 3 + ["Europe/Berlin"]
    )


_REDACTED_ZONES = """
query ($start: DateTime!, $end: DateTime!) {
  event_occurrences(window_start: $start, window_end: $end) { event_sqid redacted effective_timezone }
}
"""


def test_a_redacted_timed_occurrence_carries_its_calendars_zone_and_an_all_day_one_its_own(
    world: SimpleNamespace,
) -> None:
    """A free/busy viewer learns no event's own zone from its instants; an all-day one keeps it to lie on its days."""

    w = world
    with system_context(reason="tests.calendar.access.setup"):
        w.calendar.timezone = "Europe/Berlin"
        w.calendar.save(update_fields=["timezone", "updated_at"])
        timed = CalendarEvent.objects.create(
            calendar=w.calendar, title="Flight", timezone="Asia/Tokyo", start=_utc(22, 9), end=_utc(22, 10),
        )
        all_day = CalendarEvent.objects.create(
            calendar=w.calendar, title="Retreat", timezone="Asia/Tokyo", all_day=True,
            start=datetime(2026, 7, 23, tzinfo=ZoneInfo("Asia/Tokyo")),
            end=datetime(2026, 7, 24, tzinfo=ZoneInfo("Asia/Tokyo")),
        )
    variables = {"start": _utc(22, 0).isoformat(), "end": _utc(24, 0).isoformat()}

    def zones(seat: str) -> dict[str, tuple[bool, str]]:
        read = result_data(execute_schema(_console(), _REDACTED_ZONES, variables, user=getattr(w, seat)))
        return {row["event_sqid"]: (row["redacted"], row["effective_timezone"]) for row in read["event_occurrences"]}

    assert zones("viewer") == {timed.sqid: (True, "Europe/Berlin"), all_day.sqid: (True, "Asia/Tokyo")}
    assert zones("reader") == {timed.sqid: (False, "Asia/Tokyo"), all_day.sqid: (False, "Asia/Tokyo")}


_UPDATE_ZONE = """
mutation ($id: String!, $zone: String!) {
  update_calendar_events_by_pk(pk_columns: {id: $id}, _set: {timezone: $zone}) { start end effective_timezone }
}
"""


def test_the_event_form_changes_an_all_day_events_zone_alone_and_its_days_stay(world: SimpleNamespace) -> None:
    """The form sends the zone without the dates: the event keeps its days, re-placed at midnight in the new zone."""

    w = world
    inserted = execute_schema(
        _console(), _INSERT_ALL_DAY, {"calendar": w.calendar.sqid, "start": "2026-11-20", "end": "2026-11-21"},
        user=w.holder,
    )
    event_id = result_data(inserted)["insert_calendar_events_one"]["id"]

    updated = execute_schema(_console(), _UPDATE_ZONE, {"id": event_id, "zone": "Asia/Tokyo"}, user=w.holder)

    tokyo = ZoneInfo("Asia/Tokyo")
    with system_context(reason="tests.calendar.access.read"):
        event = CalendarEvent.objects.get(sqid=event_id)
    assert result_data(updated)["update_calendar_events_by_pk"]["effective_timezone"] == "Asia/Tokyo"
    assert (event.start, event.end) == (datetime(2026, 11, 20, tzinfo=tokyo), datetime(2026, 11, 21, tzinfo=tokyo))


@pytest.mark.parametrize(("zone", "midnight"), [
    ("", datetime(2026, 11, 22, 6, tzinfo=UTC)),
    ("Asia/Tokyo", datetime(2026, 11, 21, 15, tzinfo=UTC)),
])
def test_dragging_an_all_day_event_sends_days_the_event_places_in_its_zone(
    world: SimpleNamespace, zone: str, midnight: datetime
) -> None:
    """The drag sends the days it lands on, whatever the browser's zone; the event places them, guessing none."""

    w = world
    with system_context(reason="tests.calendar.access.setup"):
        w.calendar.timezone = zone
        w.calendar.save(update_fields=["timezone", "updated_at"])
    inserted = execute_schema(
        _console(), _INSERT_ALL_DAY, {"calendar": w.calendar.sqid, "start": "2026-11-20", "end": "2026-11-21"},
        user=w.holder,
    )
    event_id = result_data(inserted)["insert_calendar_events_one"]["id"]
    variables = {"id": event_id, "start": "2026-11-22", "end": "2026-11-23"}

    moved = result_data(execute_schema(_console(), _RESCHEDULE, variables, user=w.holder))["reschedule_event"]

    with system_context(reason="tests.calendar.access.read"):
        event = CalendarEvent.objects.get(sqid=event_id)
    assert moved["ok"] is True
    assert (event.start, event.end) == (midnight, midnight + timedelta(days=1))
