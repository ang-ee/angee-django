"""ICS calendar subscriptions through the real subscribe verb, pinned client, stream driver and calendar managers.

A scripted feed answers through integrate's ``mock_transport`` with ETag-conditional
responses; only the private-address case dials the real pinned transport, which
refuses before any connection. The writability guard is exercised on the ICS
calendar the subscription mirrors, against a local calendar.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
import pytest
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from rebac import actor_context, system_context

from angee.calendar.models import READ_ONLY_CALENDAR, SOURCE_CALENDAR_DELETE, AttendeeResponse, AttendeeRole
from angee.calendar.testing.models import Calendar, CalendarEvent, CalendarSource, EventAttendee
from angee.calendar_integrate_ics.backend import FEED_KEY, IcsCalendarBackend, IcsError
from angee.calendar_integrate_ics.connect import subscribe_calendar
from angee.integrate.states import LinkStatus
from angee.integrate.streams import advance_stream, sync_bridge
from angee.integrate.testing.http import mock_transport
from angee.integrate.testing.models import RecordLink, SyncStream
from angee.messaging.testing.models import Handle, Person
from tests.conftest import create_user

pytestmark = pytest.mark.django_db(transaction=True)

NEW_YORK = ZoneInfo("America/New_York")
BERLIN = ZoneInfo("Europe/Berlin")
FEED_URL = "https://feeds.example.test/team.ics"

_VTIMEZONE = """BEGIN:VTIMEZONE
TZID:America/New_York
BEGIN:DAYLIGHT
TZOFFSETFROM:-0500
TZOFFSETTO:-0400
TZNAME:EDT
DTSTART:19700308T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
TZNAME:EST
DTSTART:19701101T020000
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
END:STANDARD
END:VTIMEZONE"""

STANDUP = """BEGIN:VEVENT
UID:standup@example.test
DTSTAMP:20260101T000000Z
DTSTART;TZID=America/New_York:20260302T090000
DTEND;TZID=America/New_York:20260302T093000
RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=4
EXDATE;TZID=America/New_York:20260316T090000
SUMMARY:Standup
ORGANIZER;CN=Alice:mailto:alice@example.test
ATTENDEE;CN=Bob;PARTSTAT=ACCEPTED;ROLE=REQ-PARTICIPANT:mailto:bob@example.test
ATTENDEE;PARTSTAT=TENTATIVE;ROLE=OPT-PARTICIPANT:MAILTO:Carol@Example.test
ATTENDEE;PARTSTAT=ACCEPTED:mailto:alice@example.test
X-GOOGLE-CONFERENCE:https://meet.example.test/standup
TRANSP:OPAQUE
CLASS:PRIVATE
END:VEVENT"""

MOVED = """BEGIN:VEVENT
UID:standup@example.test
RECURRENCE-ID;TZID=America/New_York:20260309T090000
DTSTAMP:20260101T000000Z
DTSTART;TZID=America/New_York:20260309T100000
DURATION:PT45M
SUMMARY:Standup (moved)
END:VEVENT"""

HOLIDAY = """BEGIN:VEVENT
UID:holiday@example.test
DTSTART;VALUE=DATE:20260308
DTEND;VALUE=DATE:20260309
SUMMARY:Holiday
TRANSP:TRANSPARENT
END:VEVENT"""

BERLIN_EVENT = """BEGIN:VEVENT
UID:berlin@example.test
DTSTART;TZID=Europe/Berlin:20260329T090000
DTEND;TZID=Europe/Berlin:20260329T100000
SUMMARY:Berlin sync
STATUS:TENTATIVE
END:VEVENT"""

LAUNCH = """BEGIN:VEVENT
UID:launch@example.test
DTSTART:20260401T120000Z
DTEND:20260401T130000Z
SUMMARY:Launch
END:VEVENT"""


def _feed(*events: str) -> str:
    """Wrap VEVENTs in a New York feed calendar."""

    body = "\n".join(
        ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Example//Feed//EN", "X-WR-CALNAME:Team feed",
         "X-WR-TIMEZONE:America/New_York", _VTIMEZONE, *events, "END:VCALENDAR"]
    )
    return body.replace("\n", "\r\n") + "\r\n"


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0, *, zone: tzinfo = UTC) -> datetime:
    """Build a wall-clock moment in ``zone`` as aware UTC."""

    return datetime(year, month, day, hour, minute, tzinfo=zone).astimezone(UTC)


@dataclass
class FakeFeed:
    """A feed server that answers a matching ``If-None-Match`` with 304, and records every request."""

    body: str
    etags: bool = True
    requests: list[httpx2.Request] = field(default_factory=list)

    @property
    def etag(self) -> str:
        return f'"{hashlib.sha256(self.body.encode()).hexdigest()[:12]}"' if self.etags else ""

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.etag and request.headers.get("if-none-match") == self.etag:
            return httpx2.Response(304)
        headers = {"content-type": "text/calendar"} | ({"etag": self.etag} if self.etag else {})
        return httpx2.Response(200, content=self.body.encode(), headers=headers)


@dataclass
class Subscription:
    source: Any
    feed: FakeFeed

    def poll(self) -> int:
        """Run one bridge sync as system work, as the scheduled poll does."""

        with system_context(reason="tests.ics.poll"):
            return sync_bridge(CalendarSource.objects.get(pk=self.source.pk))

    @property
    def calendar(self) -> Any:
        """Return the feed's calendar, found through the source's calendars-stream link."""

        return self.source.calendar_for_partition(FEED_KEY)

    def stream(self, key: str = "events") -> Any:
        rows = SyncStream.objects.sudo(reason="tests.ics").filter(integration_id=self.source.pk, key=key)
        return rows.latest("generation")

    def events(self) -> dict[str, Any]:
        """Return the calendar's events by their external key."""

        links = RecordLink.objects.sudo(reason="tests.ics").filter(stream=self.stream(), target_object_id__isnull=False)
        rows = {str(row.pk): row for row in CalendarEvent.system_queryset().filter(calendar=self.calendar)}
        return {link.external_key: rows[link.target_object_id] for link in links if link.target_object_id in rows}


@pytest.fixture
def subscription() -> Iterator[Subscription]:
    feed = FakeFeed(_feed(STANDUP, MOVED, HOLIDAY, BERLIN_EVENT, LAUNCH))
    with mock_transport(feed):
        user = create_user("subscriber")
        source = subscribe_calendar(user, name="Team", url="webcal://feeds.example.test/team.ics")
        yield Subscription(source, feed)


def _snapshot(subscription: Subscription) -> dict[str, tuple[Any, ...]]:
    return {key: (event.pk, event.updated_at, event.status) for key, event in subscription.events().items()}


# --- subscribe ---------------------------------------------------------------


def test_subscribing_creates_a_source_whose_first_sync_mirrors_its_calendar(subscription: Subscription) -> None:
    source, feed = subscription.source, subscription.feed

    assert (source.backend_class, source.credential_id) == ("ics", None)
    assert source.config == {"url": FEED_URL, "timezone": "America/New_York"}
    assert (source.display_name, source.poll_interval, source.vendor.slug) == ("Team", 3600, "ics")
    assert source.sync_stage == source.SyncStage.QUEUED
    [probe] = feed.requests
    assert (str(probe.url), probe.method, probe.extensions["allow_private"]) == (FEED_URL, "GET", False)
    assert "if-none-match" not in probe.headers
    assert subscription.calendar is None

    subscription.poll()

    calendar: Any = source.calendar_for_partition(FEED_KEY)
    assert calendar is not None
    with system_context(reason="tests.ics"):
        person = Person.objects.get(user=source.owner)
        [link] = RecordLink.objects.filter(stream=subscription.stream("calendars"))
        [statement] = calendar.statements.current().filter(aspect="identity")
    assert (calendar.name, calendar.timezone, calendar.party_id, calendar.source_id) == (
        "Team", "America/New_York", person.pk, source.pk)
    assert calendar.is_writable is False
    assert (link.external_key, link.target_object_id) == (FEED_KEY, str(calendar.pk))
    assert (statement.source_kind, statement.link_id, statement.name) == ("directory", link.pk, "Team")


def test_a_remote_rename_applies_until_the_holder_renames_the_calendar(subscription: Subscription) -> None:
    subscription.poll()
    source = subscription.source

    with system_context(reason="tests.ics"):
        CalendarSource.objects.filter(pk=source.pk).update(display_name="Team feed")
    subscription.poll()
    assert subscription.calendar.name == "Team feed"

    with actor_context(source.owner):
        mine = Calendar.objects.with_actor(source.owner).get(pk=subscription.calendar.pk)
        mine.name = "My team"
        mine.save()
    with system_context(reason="tests.ics"):
        CalendarSource.objects.filter(pk=source.pk).update(display_name="Team feed v2")
    subscription.poll()

    # The holder's own statement outranks the source's directory statement.
    calendar = subscription.calendar
    assert calendar.name == "My team"
    with system_context(reason="tests.ics"):
        stated = {row.source_kind: row.name for row in calendar.statements.current().filter(aspect="identity")}
    assert stated == {"person": "My team", "directory": "Team feed v2"}


def test_a_private_feed_address_is_refused_by_the_pinned_transport() -> None:
    user = create_user("private-subscriber")

    with pytest.raises(IcsError, match="address is not allowed"):
        subscribe_calendar(user, name="Router", url="http://127.0.0.1/calendar.ics")

    assert not CalendarSource.objects.sudo(reason="tests.ics").exists()


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [(404, "", "returned HTTP 404"), (200, "not a calendar", "not valid iCalendar")],
)
def test_an_unreadable_feed_is_never_subscribed(status: int, body: str, message: str) -> None:
    user = create_user("unreadable-subscriber")
    with mock_transport(lambda request: httpx2.Response(status, content=body.encode())):
        with pytest.raises(IcsError, match=message):
            subscribe_calendar(user, name="Broken", url=FEED_URL)

    assert not CalendarSource.objects.sudo(reason="tests.ics").exists()


# --- polling -----------------------------------------------------------------


def test_the_first_poll_creates_events_and_a_replay_changes_nothing(subscription: Subscription) -> None:
    # One sync mirrors the feed's calendar, then lands its six events.
    assert subscription.poll() == 7
    keys = set(subscription.events())
    assert keys == {
        "standup@example.test",
        "standup@example.test#20260309T130000Z",
        "standup@example.test#20260316T130000Z",
        "holiday@example.test",
        "berlin@example.test",
        "launch@example.test",
    }
    before = _snapshot(subscription)
    stream = subscription.stream()
    assert stream.cursor == {"etag": subscription.feed.etag, "last_modified": ""}

    # The feed is unchanged: a 304 does no work.
    subscription.feed.requests.clear()
    assert subscription.poll() == 0
    [replay] = subscription.feed.requests
    assert replay.headers["if-none-match"] == subscription.feed.etag
    # A server without validators answers 200 with the same feed: nothing applies either.
    subscription.feed.etags = False
    assert subscription.poll() == 0
    assert _snapshot(subscription) == before
    with system_context(reason="tests.ics"):
        assert CalendarEvent.objects.filter(calendar=subscription.calendar).count() == len(before)


def test_a_changed_summary_or_time_updates_the_event(subscription: Subscription) -> None:
    subscription.poll()
    launch = subscription.events()["launch@example.test"]

    subscription.feed.body = _feed(
        STANDUP, MOVED, HOLIDAY, BERLIN_EVENT,
        LAUNCH.replace("SUMMARY:Launch", "SUMMARY:Launch review")
        .replace("DTSTART:20260401T120000Z", "DTSTART:20260401T140000Z")
        .replace("DTEND:20260401T130000Z", "DTEND:20260401T150000Z"),
    )
    assert subscription.poll() == 1

    updated = subscription.events()["launch@example.test"]
    assert updated.pk == launch.pk
    assert (updated.title, updated.start, updated.end) == ("Launch review", _utc(2026, 4, 1, 14), _utc(2026, 4, 1, 15))


def test_a_removed_uid_is_tombstoned_and_a_removed_instance_restores_its_occurrence(
    subscription: Subscription,
) -> None:
    subscription.poll()
    berlin = subscription.events()["berlin@example.test"]
    moved_key = "standup@example.test#20260309T130000Z"
    moved = subscription.events()[moved_key]

    subscription.feed.body = _feed(STANDUP, HOLIDAY, LAUNCH)
    assert subscription.poll() == 2

    links = {link.external_key: link for link in RecordLink.objects.sudo(reason="tests.ics").filter(
        stream=subscription.stream(), external_key__in=["berlin@example.test", moved_key])}
    assert {key: link.status for key, link in links.items()} == {
        "berlin@example.test": LinkStatus.TOMBSTONE,
        moved_key: LinkStatus.TOMBSTONE,
    }
    berlin.refresh_from_db()
    assert berlin.status == "cancelled"
    assert not CalendarEvent.system_queryset().filter(pk=moved.pk).exists()
    with system_context(reason="tests.ics"):
        series = CalendarEvent.objects.filter(calendar=subscription.calendar, title__startswith="Standup")
        starts = [o.start for o in series.occurrences(_utc(2026, 3, 9), _utc(2026, 3, 10))]
    assert starts == [_utc(2026, 3, 9, 9, zone=NEW_YORK)]


def test_pages_hold_the_pending_events_and_commit_the_validators_last(subscription: Subscription) -> None:
    backend = IcsCalendarBackend(CalendarSource.objects.sudo(reason="tests.ics").get(pk=subscription.source.pk))
    calendars, events = list(backend.streams())
    assert (calendars.key, calendars.partition, events.key, events.partition) == ("calendars", "", "events", FEED_KEY)
    with system_context(reason="tests.ics"):
        mirrored = advance_stream(SyncStream.objects.current(backend.bridge, calendars), backend)
        stream = SyncStream.objects.current(backend.bridge, events)
    assert (mirrored.count, mirrored.stream.cursor) == (1, {"listed": True})

    first = advance_stream(stream, backend, page_bound=4)
    assert not first.exhausted
    assert set(first.stream.cursor) == {"next", "pending"}
    # Keys sort, so a series' master lands before its instances.
    assert [key for key, _payload in first.stream.cursor["pending"]] == [
        "standup@example.test#20260309T130000Z",
        "standup@example.test#20260316T130000Z",
    ]
    subscription.feed.requests.clear()
    last = advance_stream(first.stream, backend, page_bound=4)
    assert last.exhausted and not subscription.feed.requests
    assert last.stream.cursor == {"etag": subscription.feed.etag, "last_modified": ""}
    assert len(subscription.events()) == 6


# --- mapping -----------------------------------------------------------------


def test_recurrence_id_and_exdate_become_overrides_that_expand_across_dst(subscription: Subscription) -> None:
    subscription.poll()
    events = subscription.events()
    master = events["standup@example.test"]
    moved = events["standup@example.test#20260309T130000Z"]
    excluded = events["standup@example.test#20260316T130000Z"]

    assert (master.recurrence, master.series_id) == ("FREQ=WEEKLY;BYDAY=MO;COUNT=4", None)
    assert (moved.series_id, moved.original_start, moved.title) == (master.pk, _utc(2026, 3, 9, 9, zone=NEW_YORK),
                                                                   "Standup (moved)")
    assert (moved.start, moved.end) == (_utc(2026, 3, 9, 10, zone=NEW_YORK), _utc(2026, 3, 9, 10, 45, zone=NEW_YORK))
    assert (excluded.series_id, excluded.status) == (master.pk, "cancelled")
    with system_context(reason="tests.ics"):
        occurrences = CalendarEvent.objects.filter(calendar=subscription.calendar, title__startswith="Standup")
        starts = [o.start for o in occurrences.occurrences(_utc(2026, 3, 1), _utc(2026, 4, 1))]
    # Monday 09:00 New York on both sides of the spring-forward; the 9th moved, the 16th excluded.
    assert starts == [
        _utc(2026, 3, 2, 9, zone=NEW_YORK),
        _utc(2026, 3, 9, 10, zone=NEW_YORK),
        _utc(2026, 3, 23, 9, zone=NEW_YORK),
    ]
    assert starts[0].hour == 14 and starts[2].hour == 13


def test_tzid_all_day_and_utc_moments_land_on_their_instants(subscription: Subscription) -> None:
    subscription.poll()
    events = subscription.events()
    holiday, berlin, launch = (events[key] for key in ("holiday@example.test", "berlin@example.test",
                                                       "launch@example.test"))

    # The all-day 8 March spans the New York spring-forward: a 23-hour local day.
    assert (holiday.all_day, holiday.start, holiday.end) == (
        True, _utc(2026, 3, 8, zone=NEW_YORK), _utc(2026, 3, 9, zone=NEW_YORK))
    assert holiday.end - holiday.start == timedelta(hours=23)
    assert holiday.transparency == "free"
    # 29 March 09:00 in Berlin is after the European spring-forward: 07:00 UTC.
    assert (berlin.start, berlin.end, berlin.status) == (_utc(2026, 3, 29, 7), _utc(2026, 3, 29, 8), "tentative")
    assert (launch.start, launch.end) == (_utc(2026, 4, 1, 12), _utc(2026, 4, 1, 13))
    master = events["standup@example.test"]
    assert (master.visibility, master.conference_url) == ("private", "https://meet.example.test/standup")
    # Each event keeps its DTSTART zone; a date or floating time keeps its calendar's.
    zones = {key: event.timezone for key, event in events.items()}
    assert zones == {
        "standup@example.test": "America/New_York",
        "standup@example.test#20260309T130000Z": "America/New_York",
        "standup@example.test#20260316T130000Z": "America/New_York",
        "holiday@example.test": "",
        "berlin@example.test": "Europe/Berlin",
        "launch@example.test": "UTC",
    }
    # The moved instance stated no CLASS: it keeps its master's, as the override row does.
    assert events["standup@example.test#20260309T130000Z"].visibility == "private"


def test_a_moved_master_keeps_one_override_per_instance(subscription: Subscription) -> None:
    subscription.poll()
    before = subscription.events()
    moved, excluded = before["standup@example.test#20260309T130000Z"], before["standup@example.test#20260316T130000Z"]

    # The feed moves the series an hour later and restates its instances under new keys.
    later = (
        STANDUP.replace("20260302T090000", "20260302T100000")
        .replace("20260302T093000", "20260302T103000")
        .replace("EXDATE;TZID=America/New_York:20260316T090000", "EXDATE;TZID=America/New_York:20260316T100000")
    )
    instance = MOVED.replace(
        "RECURRENCE-ID;TZID=America/New_York:20260309T090000", "RECURRENCE-ID;TZID=America/New_York:20260309T100000"
    ).replace("DTSTART;TZID=America/New_York:20260309T100000", "DTSTART;TZID=America/New_York:20260309T110000")
    subscription.feed.body = _feed(later, instance, HOLIDAY, BERLIN_EVENT, LAUNCH)
    subscription.poll()

    events = subscription.events()
    master = events["standup@example.test"]
    with system_context(reason="tests.ics"):
        overrides = sorted(CalendarEvent.objects.filter(series=master).values_list("original_start", "status", "pk"))
        stream = subscription.stream()
        links = RecordLink.objects.filter(stream=stream)
        failures = [link.external_key for link in links if link.failures(open_only=True)]
        old = RecordLink.objects.get(stream=stream, external_key="standup@example.test#20260309T130000Z")
        series = CalendarEvent.objects.filter(calendar=subscription.calendar, title__startswith="Standup")
        starts = [o.start for o in series.occurrences(_utc(2026, 3, 1), _utc(2026, 4, 1))]
    assert failures == []
    assert old.status == LinkStatus.TOMBSTONE
    # One override per instance: each new key adopted the row the series rebased.
    assert overrides == [
        (_utc(2026, 3, 9, 10, zone=NEW_YORK), "confirmed", moved.pk),
        (_utc(2026, 3, 16, 10, zone=NEW_YORK), "cancelled", excluded.pk),
    ]
    assert events["standup@example.test#20260309T140000Z"].pk == moved.pk
    assert events["standup@example.test#20260316T140000Z"].pk == excluded.pk
    assert starts == [
        _utc(2026, 3, 2, 10, zone=NEW_YORK),
        _utc(2026, 3, 9, 11, zone=NEW_YORK),
        _utc(2026, 3, 23, 10, zone=NEW_YORK),
    ]


def test_an_overlap_on_a_refusing_calendar_is_a_record_failure_not_a_failed_page(subscription: Subscription) -> None:
    subscription.poll()
    with system_context(reason="tests.ics"):
        Calendar.objects.filter(pk=subscription.calendar.pk).update(overlap_policy="refuse")
    clash = LAUNCH.replace("launch@example.test", "clash@example.test").replace("SUMMARY:Launch", "SUMMARY:Clash")
    subscription.feed.body = _feed(STANDUP, MOVED, HOLIDAY, BERLIN_EVENT, LAUNCH, clash)

    subscription.poll()

    with system_context(reason="tests.ics"):
        link = RecordLink.objects.get(stream=subscription.stream(), external_key="clash@example.test")
        [failure] = link.failures(open_only=True)
    assert failure.code == "invalid_record" and failure.details["fields"] == ["start"]
    assert "clash@example.test" not in subscription.events()
    assert "launch@example.test" in subscription.events()


def test_attendees_become_handles_with_one_organizer(subscription: Subscription) -> None:
    subscription.poll()
    master = subscription.events()["standup@example.test"]

    with system_context(reason="tests.ics"):
        rows = {
            row.handle.normalized_value: (row.role, row.response, row.handle.display_name)
            for row in EventAttendee.objects.filter(event=master).select_related("handle")
        }
        assert Handle.objects.filter(normalized_value="carol@example.test").count() == 1
    assert rows == {
        "alice@example.test": (AttendeeRole.ORGANIZER, AttendeeResponse.ACCEPTED, "Alice"),
        "bob@example.test": (AttendeeRole.REQUIRED, AttendeeResponse.ACCEPTED, "Bob"),
        "carol@example.test": (AttendeeRole.OPTIONAL, AttendeeResponse.TENTATIVE, ""),
    }


# --- writability -------------------------------------------------------------


def test_edits_on_an_ics_calendar_are_refused_and_a_local_calendar_is_unaffected(
    subscription: Subscription,
) -> None:
    subscription.poll()
    launch = subscription.events()["launch@example.test"]

    with system_context(reason="tests.ics"):
        launch.title = "Edited locally"
        with pytest.raises(ValidationError, match=READ_ONLY_CALENDAR):
            launch.full_clean()
        launch.refresh_from_db()
        with pytest.raises(ValidationError, match=READ_ONLY_CALENDAR):
            launch.reschedule(_utc(2026, 4, 2, 12), _utc(2026, 4, 2, 13))
        with pytest.raises(ValidationError, match=READ_ONLY_CALENDAR):
            CalendarEvent(calendar=subscription.calendar, title="New", start=launch.start, end=launch.end).full_clean()
        # The model names the refusal a delete preview shows; the stream's link also retains the row.
        assert launch.delete_blocker() == READ_ONLY_CALENDAR
        with pytest.raises((ValidationError, ProtectedError)):
            launch.delete()
        handle = Handle.objects.upsert(platform="email", value="dave@example.test")
        with pytest.raises(ValidationError, match=READ_ONLY_CALENDAR):
            EventAttendee(event=launch, handle=handle).full_clean()
        organizer = EventAttendee.objects.get(event__title="Standup", role=AttendeeRole.ORGANIZER)
        with pytest.raises(ValidationError, match=READ_ONLY_CALENDAR):
            organizer.delete()
        # The source's link and statements retain its calendar; the model names the refusal.
        assert subscription.calendar.delete_blocker() == SOURCE_CALENDAR_DELETE
        with pytest.raises((ValidationError, ProtectedError)):
            subscription.calendar.delete()
        occurrences = CalendarEvent.objects.filter(pk=launch.pk).occurrences(_utc(2026, 4, 1), _utc(2026, 4, 2))

        local = Calendar.objects.create(party=Person.objects.create(display_name="Local"), name="Local")
        event = CalendarEvent(calendar=local, title="Local", start=_utc(2026, 4, 1, 9), end=_utc(2026, 4, 1, 10))
        event.full_clean()
        event.save()
        event.reschedule(_utc(2026, 4, 1, 11), _utc(2026, 4, 1, 12))
        local_occurrences = CalendarEvent.objects.filter(pk=event.pk).occurrences(_utc(2026, 4, 1), _utc(2026, 4, 2))
        event.delete()

    assert [o.editable for o in occurrences] == [False]
    assert [o.editable for o in local_occurrences] == [True]
    assert local.is_writable is True
    launch.refresh_from_db()
    assert launch.title == "Launch"
    assert Calendar.system_queryset().filter(pk=subscription.calendar.pk).exists()


def test_a_local_calendar_edit_states_nothing_and_the_calendar_stays_deletable() -> None:
    user = create_user("local-holder")
    with system_context(reason="tests.ics"):
        person = Person.objects.for_user(user)
        calendar = Calendar.objects.create(party=person, name="Local", created_by_id=user.pk)

    with actor_context(user):
        mine = Calendar.objects.with_actor(user).get(pk=calendar.pk)
        mine.name = "Renamed"
        mine.save()

    with system_context(reason="tests.ics"):
        calendar.refresh_from_db()
        assert calendar.name == "Renamed"
        assert not calendar.carries_statements()
        assert calendar.delete_blocker() is None
        calendar.delete()
    assert not Calendar.system_queryset().filter(pk=calendar.pk).exists()
