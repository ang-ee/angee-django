"""ICS calendar subscriptions through the real subscribe verb, pinned client, stream driver and calendar managers.

A scripted feed answers through integrate's ``mock_transport`` with ETag-conditional
responses; only the private-address cases dial the real pinned transport, which
refuses before any connection. The replica rules (``Calendar.takes_write``) are
exercised on the ICS calendar the subscription mirrors, against a local calendar.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from time import monotonic, perf_counter
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
import icalendar
import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import ProtectedError
from django.utils import timezone
from rebac import actor_context, system_context

from angee.calendar import models as calendar_models
from angee.calendar import schema as calendar_schema
from angee.calendar.backends import ParsedCalendar
from angee.calendar.models import (
    PURGE_SOURCE_TASK,
    READ_ONLY_CALENDAR,
    SOURCE_CALENDAR_DELETE,
    AttendeeResponse,
    AttendeeRole,
)
from angee.calendar.tasks import purge_source, purge_unsubscribed_sources
from angee.calendar.testing.models import Calendar, CalendarEvent, CalendarSource, EventAttendee
from angee.calendar_integrate_ics import backend as ics_backend
from angee.calendar_integrate_ics import schema as ics_schema
from angee.calendar_integrate_ics.backend import FEED_KEY, IcsCalendarBackend, IcsError
from angee.calendar_integrate_ics.connect import subscribe_calendar
from angee.graphql.testing.documents import execute_schema, result_data
from angee.integrate.errors import IntegrationError
from angee.integrate.states import LinkStatus
from angee.integrate.streams import BridgeSyncError, advance_stream, read_stream_keys, sync_bridge
from angee.integrate.sync import sync_deadline, sync_deadline_context
from angee.integrate.testing.http import mock_transport
from angee.integrate.testing.models import RecordLink, SyncStream
from angee.jobs.celery import app as celery_app
from angee.messaging.testing.models import Handle, Person
from angee.resources.testing.models import Resource
from angee.scheduling.zones import get_zone_info
from tests.conftest import Credential, ExternalAccount, OAuthClient, Vendor, addon_schema, create_user
from tests.lock_waits import submit, wait_for_lock

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



def _feed(*events: str, zone: str = "America/New_York") -> str:
    """Wrap VEVENTs in a feed calendar whose ``X-WR-TIMEZONE`` is ``zone`` (none when blank)."""

    header = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Example//Feed//EN", "X-WR-CALNAME:Team feed"]
    if zone:
        header.append(f"X-WR-TIMEZONE:{zone}")
    body = "\n".join([*header, _VTIMEZONE, *events, "END:VCALENDAR"])
    return body.replace("\n", "\r\n") + "\r\n"


def _custom_zone_feed(standard: str, daylight: str) -> str:
    """A feed defining its own ``Custom Zone`` (US transition dates) with the given offsets."""

    zone = f"""BEGIN:VTIMEZONE
TZID:Custom Zone
BEGIN:DAYLIGHT
TZOFFSETFROM:{standard}
TZOFFSETTO:{daylight}
DTSTART:19700308T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:{daylight}
TZOFFSETTO:{standard}
DTSTART:19701101T020000
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
END:STANDARD
END:VTIMEZONE"""
    event = """BEGIN:VEVENT
UID:custom@example.test
DTSTART;TZID=Custom Zone:20260701T090000
DTEND;TZID=Custom Zone:20260701T100000
SUMMARY:Custom
END:VEVENT"""
    body = "\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:x", zone, event, "END:VCALENDAR"])
    return body.replace("\n", "\r\n") + "\r\n"


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0, *, zone: tzinfo = UTC) -> datetime:
    """Build a wall-clock moment in ``zone`` as aware UTC."""

    return datetime(year, month, day, hour, minute, tzinfo=zone).astimezone(UTC)


@dataclass
class FakeFeed:
    """A feed server that answers a matching ``If-None-Match`` with 304, and records every request."""

    body: str
    etags: bool = True
    status: int = 200
    requests: list[httpx2.Request] = field(default_factory=list)

    @property
    def etag(self) -> str:
        return f'"{hashlib.sha256(self.body.encode()).hexdigest()[:12]}"' if self.etags else ""

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx2.Response(self.status)
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

        return _poll(self.source)

    @property
    def calendar(self) -> Any:
        """Return the feed's calendar: the one calendar its source mirrors."""

        with system_context(reason="tests.ics"):
            return CalendarSource.objects.get(pk=self.source.pk).calendars.get()

    def stream(self, key: str = "events") -> Any:
        rows = SyncStream.objects.sudo(reason="tests.ics").filter(integration_id=self.source.pk, key=key)
        return rows.latest("generation")

    def events(self) -> dict[str, Any]:
        """Return the calendar's events by their external key."""

        links = RecordLink.objects.sudo(reason="tests.ics").filter(stream=self.stream(), target_object_id__isnull=False)
        rows = {str(row.pk): row for row in CalendarEvent.system_queryset().filter(calendar=self.calendar)}
        return {link.external_key: rows[link.target_object_id] for link in links if link.target_object_id in rows}

    def failures(self) -> dict[str, list[str]]:
        """Return the open failure codes of the events stream's links, by external key."""

        with system_context(reason="tests.ics"):
            links = RecordLink.objects.filter(stream=self.stream())
            return {link.external_key: [row.code for row in link.failures(open_only=True)] for link in links
                    if link.failures(open_only=True)}

    def statuses(self, *keys: str) -> dict[str, str]:
        """Return the events stream's link status of each key."""

        links = RecordLink.objects.sudo(reason="tests.ics").filter(stream=self.stream(), external_key__in=keys)
        return {link.external_key: link.status for link in links}

    def sweep(self) -> int:
        """Make the absence sweep due, as a poll interval passing does, then poll."""

        SyncStream.objects.sudo(reason="tests.ics").filter(integration_id=self.source.pk).update(
            last_reconciled_at=None
        )
        return self.poll()


def _poll(source: Any) -> int:
    with system_context(reason="tests.ics.poll"):
        return sync_bridge(CalendarSource.objects.get(pk=source.pk))


@pytest.fixture(autouse=True)
def _fresh_subscribe_rate() -> None:
    """Each test starts with an unspent subscribe rate (the default cache holds it)."""

    caches["default"].clear()


def _load_vendor() -> Any:
    """Load the addon's master tier, its ``ics`` vendor, as provisioning does."""

    return Resource.objects.load_addons(
        (apps.get_app_config("calendar_integrate_ics"),), tiers=[Resource.Tier.MASTER], allow_non_dev=True
    )


@pytest.fixture(autouse=True)
def _ics_vendor(db: None) -> None:
    """Every ICS source names the seeded ``ics`` vendor; subscribing never creates it."""

    del db
    _load_vendor()


@pytest.fixture
def march(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin now to 1 March 2026, before the feed's events, so the overlap check judges each from its start."""

    monkeypatch.setattr(timezone, "now", lambda: datetime(2026, 3, 1, tzinfo=UTC))


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


def test_the_ics_vendor_is_seeded_before_any_feed_and_adopted_by_slug() -> None:
    with system_context(reason="tests.ics"):
        vendor = Vendor.objects.seeded("ics")
        assert (vendor.display_name, vendor.icon, CalendarSource.objects.count()) == ("ICS feed", "calendar", 0)
        # A row an earlier subscription created is adopted by its slug, never duplicated.
        Vendor.objects.filter(pk=vendor.pk).delete()
        Vendor.objects.create(slug="ics", display_name="ics")
    _load_vendor()
    _load_vendor()
    with system_context(reason="tests.ics"):
        assert list(Vendor.objects.filter(slug="ics").values_list("display_name", flat=True)) == ["ICS feed"]


def test_subscribing_fetches_nothing_and_the_first_sync_mirrors_the_calendar(subscription: Subscription) -> None:
    source, feed = subscription.source, subscription.feed

    assert feed.requests == []
    # The label is the host alone: a path or file name can carry the feed's secret.
    assert (source.backend_class, source.config) == ("ics", {"feed": "feeds.example.test"})
    with system_context(reason="tests.ics"):
        assert source.credential.secret_value() == FEED_URL
    assert (source.display_name, source.poll_interval, source.vendor.slug) == ("Team", 3600, "ics")
    assert source.sync_stage == source.SyncStage.QUEUED
    with system_context(reason="tests.ics"):
        assert not CalendarSource.objects.get(pk=source.pk).calendars.exists()

    subscription.poll()

    [probe, *_rest] = feed.requests
    assert (str(probe.url), probe.method, probe.extensions["allow_private"]) == (FEED_URL, "GET", False)
    calendar: Any = subscription.calendar
    with system_context(reason="tests.ics"):
        person = Person.objects.get(user=source.owner)
        [link] = RecordLink.objects.filter(stream=subscription.stream("calendars"))
        [statement] = calendar.statements.current().filter(aspect="identity")
    assert (calendar.name, calendar.party_id, calendar.owner_id, calendar.source_id) == (
        "Team", person.pk, source.owner_id, source.pk)
    assert calendar.is_writable is False
    assert (link.external_key, link.target_object_id) == (FEED_KEY, str(calendar.pk))
    assert (statement.source_kind, statement.link_id, statement.name) == ("directory", link.pk, "Team")


def test_the_feed_address_never_reaches_the_request_log(
    subscription: Subscription, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="httpx2"):
        subscription.poll()

    logged = [record.getMessage() for record in caplog.records if record.name == "httpx2"]
    assert logged and all("team.ics" not in message for message in logged)
    assert any("https://feeds.example.test/…" in message for message in logged)


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


def test_subscribe_refuses_a_private_address_literal_without_dialling() -> None:
    user = create_user("private-subscriber")

    with pytest.raises(ValidationError) as refused:
        subscribe_calendar(user, name="Router", url="http://127.0.0.1/calendar.ics")

    assert refused.value.message_dict == {"url": ["The calendar feed address is not allowed."]}
    assert not CalendarSource.objects.sudo(reason="tests.ics").exists()


_SUBSCRIBE = """
mutation ($url: String!) { subscribe_ics_calendar(name: "", url: $url) { id } }
"""


@pytest.mark.parametrize(
    ("url", "message"),
    [
        pytest.param("not-a-feed", "Enter an http, https or webcal calendar feed address.", id="not-an-address"),
        pytest.param("ftp://feeds.example.test/team.ics", "Enter an http, https or webcal calendar feed address.",
                     id="another-scheme"),
        pytest.param("webcal://127.0.0.1/team.ics", "The calendar feed address is not allowed.", id="private-literal"),
    ],
)
def test_subscribe_refuses_an_address_as_its_url_inputs_validation_error(url: str, message: str) -> None:
    """The dialog binds the subscribe verb's own reason under the address it took, never a parser's wording."""

    console = addon_schema(calendar_schema.schemas, "console", ics_schema.schemas)

    refused = execute_schema(console, _SUBSCRIBE, {"url": url}, user=create_user("refused-subscriber"))

    [error] = refused.errors
    assert (error.message, error.extensions["code"]) == (message, "VALIDATION")
    assert error.extensions["validationErrors"] == {"url": [message]}
    assert not CalendarSource.objects.sudo(reason="tests.ics").exists()


def test_subscribe_takes_a_webcal_address_as_its_https_feed() -> None:
    console = addon_schema(calendar_schema.schemas, "console", ics_schema.schemas)
    user = create_user("webcal-subscriber")

    created = execute_schema(console, _SUBSCRIBE, {"url": "webcal://feeds.example.test/team.ics"}, user=user)

    with system_context(reason="tests.ics"):
        source = CalendarSource.objects.get(sqid=result_data(created)["subscribe_ics_calendar"]["id"])
        assert source.credential.secret_value() == "https://feeds.example.test/team.ics"


def test_the_pinned_transport_refuses_a_feed_host_that_resolves_privately() -> None:
    source = subscribe_calendar(create_user("localhost-subscriber"), name="Local", url="http://localhost/calendar.ics")

    with pytest.raises(BridgeSyncError) as refused:
        _poll(source)

    assert "address is not allowed" in str(refused.value)


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [(404, "", "HTTP 404"), (503, "", "HTTP 503"), (200, "not a calendar", "iCalendar")],
)
def test_the_first_sync_reports_an_unreadable_feed(status: int, body: str, message: str) -> None:
    feed = FakeFeed(body, status=status, etags=False)
    with mock_transport(feed):
        source = subscribe_calendar(create_user("unreadable-subscriber"), name="Broken", url=FEED_URL)
        with pytest.raises(BridgeSyncError) as refused:
            _poll(source)

    assert message in str(refused.value)


def test_the_subscriber_quota_and_rate_bound_new_subscriptions(settings: Any) -> None:
    user = create_user("busy-subscriber")
    settings.ANGEE_CALENDAR_SOURCES_PER_OWNER = 1
    subscribe_calendar(user, name="One", url=FEED_URL)
    with pytest.raises(ValidationError, match="number of connected calendars"):
        subscribe_calendar(user, name="Two", url="https://feeds.example.test/two.ics")

    settings.ANGEE_CALENDAR_SOURCES_PER_OWNER = 25
    settings.ANGEE_CALENDAR_SUBSCRIBE_RATE = (2, 3600)
    subscribe_calendar(user, name="Two", url="https://feeds.example.test/two.ics")
    with pytest.raises(ValidationError, match="Too many calendars"):
        subscribe_calendar(user, name="Three", url="https://feeds.example.test/three.ics")


def test_unsubscribing_disconnects_now_and_the_purge_job_removes_the_replica(
    subscription: Subscription, monkeypatch: pytest.MonkeyPatch
) -> None:
    subscription.poll()
    source = subscription.source
    stranger = create_user("stranger")
    calendar = subscription.calendar
    credential_id = source.credential_id
    sent: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(celery_app, "send_task", lambda name, **options: sent.append((name, options["kwargs"])))

    with system_context(reason="tests.ics"):
        rows = CalendarSource.objects
        assert rows.with_actor(source.owner).filter(pk=source.pk).exists()
        assert not rows.with_actor(stranger).filter(pk=source.pk).exists()
        CalendarSource.objects.get(pk=source.pk).unsubscribe()
        source = CalendarSource.objects.get(pk=source.pk)
        # The request only disconnects; the replica is the job's.
        assert source.lifecycle == "disconnected"
        assert CalendarEvent.objects.filter(calendar=calendar).count() == 6
        with pytest.raises(ValidationError, match="not connected"):
            source.sync_now()
    assert sent == [(PURGE_SOURCE_TASK, {"pk": source.pk})]

    assert purge_source(source.pk) == 6

    with system_context(reason="tests.ics"):
        calendar.refresh_from_db()
        source.refresh_from_db()
        links = RecordLink.objects.filter(stream=subscription.stream())
        assert calendar.is_archived is True
        assert not CalendarEvent.objects.filter(calendar=calendar).exists()
        assert not links.exclude(target_object_id=None).exists()
        assert source.credential_id is None
        assert not Credential.objects.filter(pk=credential_id).exists()


def test_a_drain_in_flight_after_unsubscribing_ends_quietly_and_writes_nothing(subscription: Subscription) -> None:
    subscription.poll()
    source = subscription.source
    subscription.feed.etags = False
    backend = IcsCalendarBackend(CalendarSource.objects.sudo(reason="tests.ics").get(pk=source.pk))
    events = SyncStream.objects.current(backend.bridge, backend.bridge.events_stream(ParsedCalendar(FEED_KEY, "Team")))
    with system_context(reason="tests.ics"):
        # A drain already running holds the feed it fetched before the unsubscribe.
        backend.read_keys(events, ["launch@example.test"])
        page = backend.extract(subscription.stream(), 100)
        CalendarSource.objects.filter(pk=source.pk).update(lifecycle="disconnected")
        source.refresh_from_db()
        assert source.purge() == 6

        # Its next extraction ends the drain quietly.
        settled = advance_stream(subscription.stream(), backend)
        assert (settled.count, settled.exhausted) == (0, True)
        # A page it extracted before the unsubscribe is refused under the stream and calendar locks.
        with pytest.raises(IntegrationError, match="unsubscribed"), transaction.atomic():
            backend.prepare_page(subscription.stream(), page)

    assert not CalendarEvent.system_queryset().filter(calendar=subscription.calendar).exists()


def test_the_reconciler_finishes_a_purge_the_unsubscribe_job_never_ran(
    subscription: Subscription, monkeypatch: pytest.MonkeyPatch
) -> None:
    subscription.poll()
    monkeypatch.setattr(celery_app, "send_task", lambda name, **options: None)
    with system_context(reason="tests.ics"):
        CalendarSource.objects.get(pk=subscription.source.pk).unsubscribe()

    assert purge_unsubscribed_sources() == 6
    assert purge_unsubscribed_sources() == 0
    with system_context(reason="tests.ics"):
        source = CalendarSource.objects.get(pk=subscription.source.pk)
        assert source.credential_id is None
        assert not CalendarEvent.objects.filter(calendar=subscription.calendar).exists()


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


def test_the_absence_sweep_tombstones_a_dropped_uid_and_a_dropped_instance_restores_its_occurrence(
    subscription: Subscription,
) -> None:
    subscription.poll()
    berlin = subscription.events()["berlin@example.test"]
    moved_key = "standup@example.test#20260309T130000Z"
    moved = subscription.events()[moved_key]
    dropped = ("berlin@example.test", moved_key)

    # The extraction pages what the feed serves; the sweep, due once per poll interval, finds what it dropped.
    subscription.feed.body = _feed(STANDUP, HOLIDAY, LAUNCH)
    assert subscription.poll() == 0
    assert subscription.statuses(*dropped) == dict.fromkeys(dropped, LinkStatus.CURRENT)
    subscription.sweep()
    assert subscription.statuses(*dropped) == dict.fromkeys(dropped, LinkStatus.UNAVAILABLE)
    berlin.refresh_from_db()
    assert berlin.status == "tentative"
    subscription.sweep()
    assert subscription.statuses(*dropped) == dict.fromkeys(dropped, LinkStatus.TOMBSTONE)

    berlin.refresh_from_db()
    assert berlin.status == "cancelled"
    assert not CalendarEvent.system_queryset().filter(pk=moved.pk).exists()
    with system_context(reason="tests.ics"):
        series = CalendarEvent.objects.filter(calendar=subscription.calendar, title__startswith="Standup")
        starts = [o.start for o in series.occurrences(_utc(2026, 3, 9), _utc(2026, 3, 10))]
    assert starts == [_utc(2026, 3, 9, 9, zone=NEW_YORK)]


def test_pages_carry_only_the_last_key_and_commit_the_validators_last(subscription: Subscription) -> None:
    backend = IcsCalendarBackend(CalendarSource.objects.sudo(reason="tests.ics").get(pk=subscription.source.pk))
    calendars, events = list(backend.streams())
    assert (calendars.key, calendars.partition, events.key, events.partition) == ("calendars", "", "events", FEED_KEY)
    assert events.reconcile_interval == timedelta(hours=1)
    with system_context(reason="tests.ics"):
        mirrored = advance_stream(SyncStream.objects.current(backend.bridge, calendars), backend)
        stream = SyncStream.objects.current(backend.bridge, events)
    assert (mirrored.count, mirrored.stream.cursor) == (1, {"listed": True})

    first = advance_stream(stream, backend, page_bound=4)
    assert not first.exhausted
    # Keys sort, so a series' master lands before its instances; the cursor holds no payload.
    assert first.stream.cursor == {
        "after": "standup@example.test",
        "next": {"etag": subscription.feed.etag, "last_modified": ""},
    }
    subscription.feed.requests.clear()
    last = advance_stream(first.stream, backend, page_bound=4)
    # The adapter's later pages read the snapshot its first page fetched.
    assert last.exhausted and not subscription.feed.requests
    assert last.stream.cursor == {"etag": subscription.feed.etag, "last_modified": ""}
    assert len(subscription.events()) == 6


def test_identity_reads_reread_the_feed_and_a_dropped_key_is_a_tombstone(subscription: Subscription) -> None:
    subscription.poll()
    backend = IcsCalendarBackend(CalendarSource.objects.sudo(reason="tests.ics").get(pk=subscription.source.pk))
    subscription.feed.body = _feed(STANDUP, MOVED, HOLIDAY, LAUNCH)
    subscription.feed.requests.clear()

    with system_context(reason="tests.ics"):
        changes = backend.read_keys(subscription.stream(), ["launch@example.test", "berlin@example.test"])
        [calendar] = backend.read_keys(subscription.stream("calendars"), [FEED_KEY])

    assert backend.supports_identity_reads is True
    assert [(change.external_key, change.tombstone) for change in changes] == [
        ("launch@example.test", False),
        ("berlin@example.test", True),
    ]
    # One unconditional fetch serves every requested key.
    [read] = subscription.feed.requests
    assert "if-none-match" not in read.headers
    assert (calendar.external_key, calendar.tombstone) == (FEED_KEY, False)


# --- mapping -----------------------------------------------------------------


def test_recurrence_id_and_exdate_become_overrides_that_expand_across_dst(subscription: Subscription) -> None:
    subscription.poll()
    events = subscription.events()
    master = events["standup@example.test"]
    moved = events["standup@example.test#20260309T130000Z"]
    excluded = events["standup@example.test#20260316T130000Z"]

    assert (master.recurrence, master.series_id) == ("FREQ=WEEKLY;COUNT=4;BYDAY=MO", None)
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
    # Each event keeps its DTSTART zone; a date keeps the feed's X-WR-TIMEZONE.
    zones = {key: event.timezone for key, event in events.items()}
    assert zones == {
        "standup@example.test": "America/New_York",
        "standup@example.test#20260309T130000Z": "America/New_York",
        "standup@example.test#20260316T130000Z": "America/New_York",
        "holiday@example.test": "America/New_York",
        "berlin@example.test": "Europe/Berlin",
        "launch@example.test": "UTC",
    }
    # The moved instance stated no CLASS: it keeps its master's.
    assert events["standup@example.test#20260309T130000Z"].visibility == "private"


def test_two_feeds_defining_one_tzid_differently_each_keep_their_own_zone() -> None:
    first, second = FakeFeed(_custom_zone_feed("-0500", "-0400")), FakeFeed(_custom_zone_feed("+0100", "+0200"))
    starts = []
    for feed, name in ((first, "first"), (second, "second")):
        with mock_transport(feed):
            source = subscribe_calendar(create_user(f"{name}-zone"), name=name, url=f"https://{name}.example.test/a.ics")
            _poll(source)
        with system_context(reason="tests.ics"):
            calendar = CalendarSource.objects.get(pk=source.pk).calendars.get()
            starts.append(CalendarEvent.objects.get(calendar=calendar).start)

    # 09:00 in July: daylight time in each feed's own definition of "Custom Zone".
    assert starts == [_utc(2026, 7, 1, 13), _utc(2026, 7, 1, 7)]


def test_a_windows_tzid_maps_to_its_iana_zone(subscription: Subscription) -> None:
    windows = """BEGIN:VEVENT
UID:windows@example.test
DTSTART;TZID=Eastern Standard Time:20260701T090000
DTEND;TZID=Eastern Standard Time:20260701T100000
SUMMARY:Windows
END:VEVENT"""
    subscription.feed.body = _feed(windows)
    subscription.poll()

    event = subscription.events()["windows@example.test"]
    assert (event.timezone, event.start) == ("America/New_York", _utc(2026, 7, 1, 13))


def test_an_overlap_on_a_refusing_calendar_is_a_record_failure_not_a_failed_page(
    march: None, subscription: Subscription
) -> None:
    del march
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


def test_feed_shapes_take_the_event_owners_stored_form(subscription: Subscription) -> None:
    weekly = """BEGIN:VEVENT
UID:offsite@example.test
DTSTART;VALUE=DATE:20260301
DTEND;VALUE=DATE:20260302
RRULE:FREQ=WEEKLY;COUNT=3
EXDATE;VALUE=DATE:20260308
EXDATE;VALUE=DATE:20260310
END:VEVENT"""
    subscription.feed.body = _feed(weekly)
    subscription.poll()

    events = subscription.events()
    master = events["offsite@example.test"]
    excluded = events["offsite@example.test#20260308T050000Z"]
    # An untitled event takes the owner's default title; its cancelled instance follows it.
    assert (master.title, excluded.title, excluded.status) == ("(No title)", "(No title)", "cancelled")
    # 10 March is no instance of the rule: its EXDATE is dropped at parse.
    assert set(events) == {"offsite@example.test", "offsite@example.test#20260308T050000Z"}
    # The 8 March all-day instance is the spring-forward day: its local day lasts 23 hours.
    assert (excluded.start, excluded.end) == (_utc(2026, 3, 8, zone=NEW_YORK), _utc(2026, 3, 9, zone=NEW_YORK))
    assert excluded.end - excluded.start == timedelta(hours=23)


def test_attendees_land_on_handles_by_their_given_address_and_never_rename_one(
    subscription: Subscription, monkeypatch: pytest.MonkeyPatch
) -> None:
    with system_context(reason="tests.ics"):
        bob = Handle.objects.upsert(platform="email", value="bob@example.test", display_name="Robert")
    monkeypatch.setattr(calendar_models, "ATTENDEES_PER_EVENT", 2)

    subscription.poll()

    with system_context(reason="tests.ics"):
        master = subscription.events()["standup@example.test"]
        rows = list(EventAttendee.objects.filter(event=master).select_related("handle").order_by("role"))
        bob.refresh_from_db()
        carol = Handle.objects.filter(normalized_value="carol@example.test").first()
    # The organizer first, then the feed's order, cut at the cap: Carol is not kept.
    assert sorted((row.handle.normalized_value, row.role) for row in rows) == [
        ("alice@example.test", AttendeeRole.ORGANIZER),
        ("bob@example.test", AttendeeRole.REQUIRED),
    ]
    assert carol is None
    assert bob.display_name == "Robert"


def test_attendees_keep_the_address_the_feed_gave(subscription: Subscription) -> None:
    subscription.poll()

    with system_context(reason="tests.ics"):
        carol = Handle.objects.get(normalized_value="carol@example.test")
    assert carol.value == "Carol@Example.test"


def test_a_feed_over_the_event_cap_fails_its_poll(subscription: Subscription, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ics_backend, "_EVENTS_PER_FEED", 2)

    with pytest.raises(BridgeSyncError) as refused:
        subscription.poll()

    assert "more than 2 events" in str(refused.value)


def test_a_feed_rule_beyond_the_fields_bounds_is_an_invalid_event(subscription: Subscription) -> None:
    minutely = LAUNCH.replace("launch@example.test", "minutely@example.test") + "\n"
    minutely = minutely.replace("SUMMARY:Launch", "SUMMARY:Minutely\nRRULE:FREQ=MINUTELY;COUNT=5")
    endless = LAUNCH.replace("launch@example.test", "endless@example.test").replace(
        "SUMMARY:Launch", "SUMMARY:Endless\nRRULE:FREQ=DAILY;COUNT=60000"
    )
    subscription.feed.body = _feed(STANDUP, MOVED, HOLIDAY, BERLIN_EVENT, LAUNCH, minutely.strip(), endless)

    subscription.poll()

    with system_context(reason="tests.ics"):
        refused = {
            key: [(row.code, row.details.get("fields")) for row in RecordLink.objects.get(
                stream=subscription.stream(), external_key=key).failures(open_only=True)]
            for key in ("minutely@example.test", "endless@example.test")
        }
    # The event's own field refuses each rule at parse, before anything expands it.
    assert refused == {
        "minutely@example.test": [("invalid_event", None)],
        "endless@example.test": [("invalid_event", None)],
    }
    assert "launch@example.test" in subscription.events()


def test_a_status_only_change_derives_no_series_end(
    subscription: Subscription, monkeypatch: pytest.MonkeyPatch
) -> None:
    subscription.poll()
    series_end = subscription.events()["standup@example.test"].series_end

    def refuse(self: Any) -> None:
        raise AssertionError("a status-only save derived the series end")

    monkeypatch.setattr(CalendarEvent, "_derive_series_end", refuse)
    subscription.feed.body = _feed(
        STANDUP.replace("SUMMARY:Standup", "SUMMARY:Standup\nSTATUS:TENTATIVE"), MOVED, HOLIDAY, BERLIN_EVENT, LAUNCH
    )
    assert subscription.poll() == 1

    master = subscription.events()["standup@example.test"]
    assert (master.status, master.series_end) == ("tentative", series_end)


def test_an_exdate_never_expands_a_rule_past_the_owners_bound(subscription: Subscription) -> None:
    hourly = """BEGIN:VEVENT
UID:hourly@example.test
DTSTART:20260401T000000Z
DTEND:20260401T003000Z
RRULE:FREQ=HOURLY
EXDATE:21000101T000000Z
SUMMARY:Hourly
END:VEVENT"""
    minutely = """BEGIN:VEVENT
UID:minutely@example.test
DTSTART:20260401T000000Z
DTEND:20260401T000100Z
RRULE:FREQ=MINUTELY
EXDATE:99991231T000000Z
SUMMARY:Minutely
END:VEVENT"""
    subscription.feed.body = _feed(STANDUP, MOVED, HOLIDAY, BERLIN_EVENT, LAUNCH, hourly, minutely)

    subscription.poll()

    # The field's daily floor refuses both rules before anything expands them.
    assert subscription.failures() == {
        "hourly@example.test": ["invalid_event"],
        "minutely@example.test": ["invalid_event"],
    }
    assert "launch@example.test" in subscription.events()


def test_cancelled_instances_count_against_the_feed_cap(
    subscription: Subscription, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Five VEVENTs pass the count before parsing; with its cancelled 16 March instance the feed produces six.
    monkeypatch.setattr(ics_backend, "_EVENTS_PER_FEED", 5)

    with pytest.raises(BridgeSyncError) as refused:
        subscription.poll()

    assert "more than 5 events" in str(refused.value)


def test_a_feed_over_the_time_zone_cap_fails_before_it_is_parsed(
    subscription: Subscription, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ics_backend, "_TIMEZONES_PER_FEED", 0)

    with pytest.raises(BridgeSyncError) as refused:
        subscription.poll()

    assert "more than 0 time zones" in str(refused.value)


def test_a_floating_until_at_the_end_of_time_on_a_zone_behind_utc_lands(subscription: Subscription) -> None:
    forever = """BEGIN:VEVENT
UID:forever@example.test
DTSTART;TZID=America/New_York:20260105T090000
DTEND;TZID=America/New_York:20260105T100000
RRULE:FREQ=YEARLY;UNTIL=99991231T235959
SUMMARY:Forever
END:VEVENT"""
    subscription.feed.body = _feed(STANDUP, MOVED, HOLIDAY, BERLIN_EVENT, LAUNCH, forever)

    subscription.poll()

    assert subscription.failures() == {}
    # 9999-12-31 23:59:59 in New York is past the last UTC instant: the series ends with the representable range.
    assert subscription.events()["forever@example.test"].series_end == _utc(9999, 1, 5, 10, zone=NEW_YORK)


def test_a_far_future_event_lands_on_a_refusing_calendar_beside_an_open_yearly_series_ahead_of_utc(
    subscription: Subscription,
) -> None:
    subscription.poll()
    with system_context(reason="tests.ics"):
        Calendar.objects.filter(pk=subscription.calendar.pk).update(overlap_policy="refuse")
    early = """BEGIN:VEVENT
UID:early@example.test
DTSTART;TZID=Europe/Berlin:20260401T070000
DTEND;TZID=Europe/Berlin:20260401T073000
RRULE:FREQ=YEARLY
SUMMARY:Early
END:VEVENT"""
    far = """BEGIN:VEVENT
UID:far@example.test
DTSTART:99991228T220000Z
DTEND:99991228T230000Z
SUMMARY:Far
END:VEVENT"""
    subscription.feed.body = _feed(STANDUP, MOVED, HOLIDAY, BERLIN_EVENT, LAUNCH, early, far)

    subscription.poll()

    assert subscription.failures() == {}
    assert {"early@example.test", "far@example.test"} <= set(subscription.events())


def test_a_custom_tzid_falls_back_to_the_feeds_zone_and_only_a_slash_tzid_reads_a_vendor_prefix(
    subscription: Subscription,
) -> None:
    def event(uid: str, tzid: str) -> str:
        return (
            f"BEGIN:VEVENT\nUID:{uid}\nDTSTART;TZID={tzid}:20260701T090000\n"
            f"DTEND;TZID={tzid}:20260701T100000\nSUMMARY:{uid}\nEND:VEVENT"
        )

    subscription.feed.body = _feed(
        event("custom@example.test", "Customized Time Zone"),
        event("vendor@example.test", "/example.com/Europe/Berlin"),
        event("unprefixed@example.test", "Mars/Europe/Berlin"),
    )
    subscription.poll()

    events = subscription.events()
    zones = {key: (events[key].timezone, events[key].start) for key in events}
    # Unmappable TZIDs read the feed's X-WR-TIMEZONE (New York); a /-prefixed one names its IANA zone.
    assert zones == {
        "custom@example.test": ("America/New_York", _utc(2026, 7, 1, 13)),
        "vendor@example.test": ("Europe/Berlin", _utc(2026, 7, 1, 7)),
        "unprefixed@example.test": ("America/New_York", _utc(2026, 7, 1, 13)),
    }


def test_an_adapter_holding_a_snapshot_pages_from_it_without_fetching_again(subscription: Subscription) -> None:
    subscription.poll()
    backend = IcsCalendarBackend(CalendarSource.objects.sudo(reason="tests.ics").get(pk=subscription.source.pk))
    subscription.feed.requests.clear()

    with system_context(reason="tests.ics"):
        backend.read_keys(subscription.stream(), ["launch@example.test"])
        settled = advance_stream(subscription.stream(), backend)

    # One fetch serves the identity read; the extraction finds the cursor already holds its validators.
    assert len(subscription.feed.requests) == 1
    assert (settled.count, settled.stream.cursor) == (0, {"etag": subscription.feed.etag, "last_modified": ""})


def test_an_unreadable_instance_quarantines_under_a_key_that_fits_its_link(subscription: Subscription) -> None:
    subscription.poll()
    uid = "y" * 490 + "@example.test"
    broken = f"BEGIN:VEVENT\nUID:{uid}\nRECURRENCE-ID:garbage\nDTSTART:20260401T120000Z\nSUMMARY:Broken\nEND:VEVENT"
    subscription.feed.body = _feed(STANDUP, MOVED, HOLIDAY, BERLIN_EVENT, LAUNCH, broken)

    subscription.poll()

    failures = subscription.failures()
    [key] = failures
    assert failures[key] == ["invalid_event"] and key.startswith("sha256:") and len(key) <= 512


def test_a_handle_merge_leaves_the_replica_converged_with_its_feed(subscription: Subscription) -> None:
    subscription.poll()
    with system_context(reason="tests.ics"):
        bob = Handle.objects.get(normalized_value="bob@example.test")
        bob.fold([Handle.objects.get(normalized_value="carol@example.test")])

    # The feed still names Carol: on both sides she is Bob now, so the series settles once with no conflict.
    subscription.feed.body = _feed(
        STANDUP, MOVED, HOLIDAY, BERLIN_EVENT, LAUNCH.replace("SUMMARY:Launch", "SUMMARY:Launch review")
    )
    assert subscription.poll() == 2
    subscription.feed.etags = False
    assert subscription.poll() == 0
    subscription.sweep()
    assert subscription.poll() == 0
    assert subscription.failures() == {}


def test_a_rule_that_never_occurs_is_refused_at_parse_at_once(subscription: Subscription) -> None:
    never = """BEGIN:VEVENT
UID:never@example.test
DTSTART;TZID=America/New_York:20260101T090000
DTEND;TZID=America/New_York:20260101T100000
RRULE:FREQ=DAILY;BYMONTH=2;BYMONTHDAY=30
EXDATE;TZID=America/New_York:99990101T090000
SUMMARY:February 30
END:VEVENT"""
    subscription.feed.body = _feed(STANDUP, MOVED, HOLIDAY, BERLIN_EVENT, LAUNCH, never)

    began = perf_counter()
    subscription.poll()

    assert subscription.failures() == {"never@example.test": ["invalid_event"]}
    assert perf_counter() - began < 30


def test_a_far_recurrence_id_on_an_open_daily_series_is_judged_by_its_own_window(subscription: Subscription) -> None:
    daily = """BEGIN:VEVENT
UID:daily@example.test
DTSTART:20260401T080000Z
DTEND:20260401T081500Z
RRULE:FREQ=DAILY
SUMMARY:Daily
END:VEVENT"""
    far = """BEGIN:VEVENT
UID:daily@example.test
RECURRENCE-ID:22000401T080000Z
DTSTART:22000401T090000Z
DTEND:22000401T091500Z
SUMMARY:Far instance
END:VEVENT"""
    subscription.feed.body = _feed(STANDUP, MOVED, HOLIDAY, BERLIN_EVENT, LAUNCH, daily, far)

    subscription.poll()

    # R-R3: the instance check reads the period before the instance, not 174 years of history.
    assert subscription.failures() == {}
    assert subscription.events()["daily@example.test#22000401T080000Z"].title == "Far instance"


@pytest.mark.parametrize(
    "event",
    [
        pytest.param(
            "UID:edge@example.test\nDTSTART;VALUE=DATE:99991230\nRRULE:FREQ=DAILY;COUNT=2\nSUMMARY:Edge",
            id="daily-at-the-end-of-time",
        ),
        pytest.param(
            "UID:edge@example.test\nDTSTART;VALUE=DATE:20261228\nDTEND;VALUE=DATE:20270104\n"
            "RRULE:FREQ=YEARLY;UNTIL=99991231\nSUMMARY:Edge",
            id="yearly-seven-days",
        ),
        pytest.param(
            "UID:edge@example.test\nDTSTART;VALUE=DATE:00010101\nSUMMARY:Edge",
            id="year-one",
        ),
    ],
)
def test_an_event_past_the_representable_dates_is_refused_on_a_refusing_calendar(
    subscription: Subscription, event: str
) -> None:
    subscription.poll()
    with system_context(reason="tests.ics"):
        Calendar.objects.filter(pk=subscription.calendar.pk).update(overlap_policy="refuse")
    subscription.feed.body = _feed(STANDUP, MOVED, HOLIDAY, BERLIN_EVENT, LAUNCH, f"BEGIN:VEVENT\n{event}\nEND:VEVENT")

    subscription.poll()

    assert list(subscription.failures()) == ["edge@example.test"]
    assert "launch@example.test" in subscription.events()


def test_a_handle_a_refused_record_created_is_never_reused_by_the_next(subscription: Subscription) -> None:
    subscription.poll()
    long_address = "x" * 600 + "@example.test"
    refused = (
        "BEGIN:VEVENT\nUID:a-refused@example.test\nDTSTART:20260402T080000Z\nDTEND:20260402T090000Z\n"
        f"SUMMARY:Refused\nATTENDEE:mailto:fresh@example.test\nATTENDEE:mailto:{long_address}\nEND:VEVENT"
    )
    landed = (
        "BEGIN:VEVENT\nUID:b-landed@example.test\nDTSTART:20260402T100000Z\nDTEND:20260402T110000Z\n"
        "SUMMARY:Landed\nATTENDEE:mailto:fresh@example.test\nEND:VEVENT"
    )
    subscription.feed.body = _feed(STANDUP, MOVED, HOLIDAY, BERLIN_EVENT, LAUNCH, refused, landed)

    subscription.poll()

    assert subscription.failures() == {"a-refused@example.test": ["invalid_record"]}
    with system_context(reason="tests.ics"):
        [row] = EventAttendee.objects.filter(event=subscription.events()["b-landed@example.test"])
        assert Handle.objects.filter(pk=row.handle_id, value="fresh@example.test").exists()


def test_a_feed_zone_built_in_unbounded_memory_falls_back_to_the_calendars_zone(subscription: Subscription) -> None:
    runaway = """BEGIN:VTIMEZONE
TZID:Runaway
BEGIN:STANDARD
DTSTART:19700101T000000
TZOFFSETFROM:+0100
TZOFFSETTO:+0100
RRULE:FREQ=SECONDLY
END:STANDARD
END:VTIMEZONE"""
    event = """BEGIN:VEVENT
UID:runaway@example.test
DTSTART;TZID=Runaway:20260701T090000
DTEND;TZID=Runaway:20260701T100000
SUMMARY:Runaway
END:VEVENT"""
    zone = runaway.replace("\n", "\r\n") + "\r\n"
    subscription.feed.body = _feed(event, zone="").replace("END:VTIMEZONE\r\n", "END:VTIMEZONE\r\n" + zone, 1)

    subscription.poll()

    # Without an X-WR-TIMEZONE, an unbuilt feed zone reads in the calendar's zone (here the project's).
    calendar_zone = get_zone_info(subscription.calendar.timezone)
    assert subscription.events()["runaway@example.test"].start == _utc(2026, 7, 1, 9, zone=calendar_zone)


def test_a_parameterised_begin_line_counts_against_the_time_zone_cap(
    subscription: Subscription, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ics_backend, "_TIMEZONES_PER_FEED", 0)
    subscription.feed.body = subscription.feed.body.replace("BEGIN:VTIMEZONE", "BEGIN;X-NOTE=1:VTIMEZONE")

    with pytest.raises(BridgeSyncError) as refused:
        subscription.poll()

    assert "more than 0 time zones" in str(refused.value)


def test_a_handle_merge_converges_whichever_attendee_the_feed_lists_first(subscription: Subscription) -> None:
    carol_first = STANDUP.replace(
        "ATTENDEE;CN=Bob;PARTSTAT=ACCEPTED;ROLE=REQ-PARTICIPANT:mailto:bob@example.test\n"
        "ATTENDEE;PARTSTAT=TENTATIVE;ROLE=OPT-PARTICIPANT:MAILTO:Carol@Example.test",
        "ATTENDEE;PARTSTAT=TENTATIVE;ROLE=OPT-PARTICIPANT:MAILTO:Carol@Example.test\n"
        "ATTENDEE;CN=Bob;PARTSTAT=ACCEPTED;ROLE=REQ-PARTICIPANT:mailto:bob@example.test",
    )
    subscription.feed.body = _feed(carol_first, MOVED, HOLIDAY, BERLIN_EVENT, LAUNCH)
    subscription.poll()
    with system_context(reason="tests.ics"):
        bob = Handle.objects.get(normalized_value="bob@example.test")
        bob.fold([Handle.objects.get(normalized_value="carol@example.test")])

    # Carol comes first, yet the projection collapses the pair exactly as the merge did.
    subscription.feed.body = _feed(
        carol_first, MOVED, HOLIDAY, BERLIN_EVENT, LAUNCH.replace("SUMMARY:Launch", "SUMMARY:Launch review")
    )
    assert subscription.poll() == 2
    subscription.feed.etags = False
    assert subscription.poll() == 0
    subscription.sweep()
    assert subscription.poll() == 0
    assert subscription.failures() == {}


def _make_unexpandable(event: Any, **columns: Any) -> None:
    """Store columns no save path admits now, as a row written before the rule would be."""

    CalendarEvent.system_queryset().filter(pk=event.pk).update(series_end=None, **columns)


@pytest.mark.parametrize(
    ("columns", "window"),
    [
        pytest.param(
            # A COUNT series is read from its start (its count is the cap's); this one passes it.
            {"start": datetime(2000, 1, 1, 9, tzinfo=UTC), "end": datetime(2000, 1, 1, 10, tzinfo=UTC),
             "recurrence": "FREQ=DAILY;COUNT=20000"},
            (datetime(2050, 3, 1, tzinfo=UTC), datetime(2050, 4, 2, tzinfo=UTC)),
            id="series-too-long",
        ),
        pytest.param(
            # The 28 December occurrence would end four days later, past the last date.
            {"start": datetime(9999, 12, 25, tzinfo=UTC), "end": datetime(9999, 12, 29, tzinfo=UTC),
             "recurrence": "FREQ=DAILY;COUNT=5"},
            (datetime(9999, 12, 20, tzinfo=UTC), datetime(9999, 12, 29, 12, tzinfo=UTC)),
            id="occurrence-end-out-of-range",
        ),
    ],
)
def test_a_row_the_calendar_cannot_expand_never_fails_a_read_or_a_save_beside_it(
    march: None,
    subscription: Subscription,
    columns: dict[str, Any],
    window: tuple[datetime, datetime],
    caplog: pytest.LogCaptureFixture,
) -> None:
    del march
    subscription.poll()
    with system_context(reason="tests.ics"):
        Calendar.objects.filter(pk=subscription.calendar.pk).update(overlap_policy="refuse")
        _make_unexpandable(subscription.events()["berlin@example.test"], **columns)
        with caplog.at_level(logging.DEBUG, logger="angee.calendar.models"):
            shown = CalendarEvent.objects.filter(calendar=subscription.calendar).occurrences(*window)

    # The read returns the other rows; the skipped one is noted once, at debug level.
    assert all(occurrence.title != "Berlin sync" for occurrence in shown)
    assert len([record for record in caplog.records if "cannot be expanded" in record.getMessage()]) == 1
    if window[0].year == 2050:
        assert all(occurrence.title != "Berlin sync" for occurrence in shown)
    # A new event saved to the refusing calendar, in a window where the row is readable, is judged against it.
    late = LAUNCH.replace("launch@example.test", "late@example.test").replace(
        "DTSTART:20260401T120000Z", "DTSTART:20260402T120000Z"
    ).replace("DTEND:20260401T130000Z", "DTEND:20260402T130000Z")
    clash = LAUNCH.replace("launch@example.test", "clash@example.test").replace("SUMMARY:Launch", "SUMMARY:Clash")
    subscription.feed.body = _feed(STANDUP, MOVED, HOLIDAY, LAUNCH, late, clash)
    subscription.poll()
    assert "late@example.test" in subscription.events()
    # A real overlap beside the skipped row is still refused.
    assert subscription.failures() == {"clash@example.test": ["invalid_record"]}


def test_saving_an_open_series_past_the_save_time_cap_is_refused(subscription: Subscription) -> None:
    old = """BEGIN:VEVENT
UID:old@example.test
DTSTART:19950101T090000Z
DTEND:19950101T100000Z
RRULE:FREQ=DAILY
SUMMARY:Since 1995
END:VEVENT"""
    subscription.feed.body = _feed(STANDUP, MOVED, HOLIDAY, BERLIN_EVENT, LAUNCH, old)

    subscription.poll()

    assert subscription.failures() == {"old@example.test": ["invalid_record"]}


def _zone(name: str, rule: str, *, start: str = "19700101T000000", nested: str = "") -> str:
    return (
        f"BEGIN:VTIMEZONE\nTZID:{name}\nBEGIN:STANDARD\nDTSTART:{start}\nTZOFFSETFROM:+0100\n"
        f"TZOFFSETTO:+0100\nRRULE:{rule}\n{nested}END:STANDARD\nEND:VTIMEZONE"
    )


def test_a_feed_zone_counts_its_true_transitions(subscription: Subscription) -> None:
    year_one = icalendar.Calendar.from_ical(_zone("Ancient", "FREQ=YEARLY", start="00010101T000000").encode())
    [ancient] = year_one.walk("VTIMEZONE")
    # Every year from 1 to icalendar's 2038 end is a transition.
    assert ics_backend._transitions(ancient, 10_000) == 2038

    days = ",".join(str(day) for day in range(1, 29))
    every_day = f"FREQ=YEARLY;BYMONTH=1,2,3,4,5,6,7,8,9,10,11,12;BYMONTHDAY={days}"
    event = (
        "BEGIN:VEVENT\nUID:busy@example.test\nDTSTART;TZID=Busy:20260701T090000\n"
        "DTEND;TZID=Busy:20260701T100000\nSUMMARY:Busy\nEND:VEVENT"
    )
    defined = _zone("Busy", every_day).replace("\n", "\r\n") + "\r\n"
    subscription.feed.body = _feed(event, zone="").replace("END:VTIMEZONE\r\n", "END:VTIMEZONE\r\n" + defined, 1)
    subscription.poll()

    # A yearly rule whose BY parts unroll every day for 69 years: never built, read in the calendar's zone.
    calendar_zone = get_zone_info(subscription.calendar.timezone)
    assert subscription.events()["busy@example.test"].start == _utc(2026, 7, 1, 9, zone=calendar_zone)

    # A nested observance counts too (icalendar's own parse refuses one in a feed, so it is built here).
    class Observance(icalendar.Component):
        name = "X-OBSERVANCE"

    nested_zone = icalendar.Timezone({"TZID": "Nested"})
    standard = icalendar.TimezoneStandard()
    standard.add("DTSTART", datetime(1970, 1, 1))
    standard.add("RRULE", {"FREQ": ["YEARLY"]})
    nested = Observance()
    nested.add("DTSTART", datetime(1970, 1, 1))
    nested.add("RRULE", {"FREQ": ["SECONDLY"]})
    standard.add_component(nested)
    nested_zone.add_component(standard)
    # The standard observance alone is 69 yearly transitions; its nested secondly rule is refused.
    assert ics_backend._transitions(standard, 5_000) > 5_000
    assert ics_backend._transitions(nested_zone, 5_000) > 5_000
    nested_zone.subcomponents[0].subcomponents.clear()
    assert ics_backend._transitions(nested_zone, 5_000) == 69


def test_two_subscriptions_to_one_feed_share_a_credential_and_one_purge_spares_the_other(
    subscription: Subscription, monkeypatch: pytest.MonkeyPatch
) -> None:
    subscription.poll()
    monkeypatch.setattr(celery_app, "send_task", lambda name, **options: None)
    owner = subscription.source.owner
    twin = subscribe_calendar(owner, name="Team again", url="webcal://feeds.example.test/team.ics")
    assert twin.credential_id == subscription.source.credential_id
    _poll(twin)

    with system_context(reason="tests.ics"):
        CalendarSource.objects.get(pk=subscription.source.pk).unsubscribe()
    assert purge_unsubscribed_sources() == 6

    # The twin keeps the shared credential and still syncs.
    with system_context(reason="tests.ics"):
        assert Credential.objects.filter(pk=twin.credential_id).exists()
    subscription.feed.etags = False
    _poll(twin)

    # Purging the last subscriber retires it.
    with system_context(reason="tests.ics"):
        CalendarSource.objects.get(pk=twin.pk).unsubscribe()
    assert purge_unsubscribed_sources() == 6
    with system_context(reason="tests.ics"):
        assert not Credential.objects.filter(pk=twin.credential_id).exists()


def test_the_reconciler_goes_on_past_a_source_whose_purge_fails(
    subscription: Subscription, monkeypatch: pytest.MonkeyPatch
) -> None:
    subscription.poll()
    monkeypatch.setattr(celery_app, "send_task", lambda name, **options: None)
    with mock_transport(FakeFeed(_feed(LAUNCH))):
        other = subscribe_calendar(create_user("other-subscriber"), name="Other", url="https://feeds.example.test/b.ics")
        _poll(other)
    with system_context(reason="tests.ics"):
        CalendarSource.objects.get(pk=subscription.source.pk).unsubscribe()
        CalendarSource.objects.get(pk=other.pk).unsubscribe()
    purge = CalendarSource.purge

    def failing(self: Any) -> int:
        if self.pk == subscription.source.pk:
            raise RuntimeError("purge failed")
        return purge(self)

    monkeypatch.setattr(CalendarSource, "purge", failing)

    assert purge_unsubscribed_sources() == 1
    with system_context(reason="tests.ics"):
        failed = CalendarSource.objects.get(pk=subscription.source.pk)
    assert failed.runtime_status == "error"


def test_every_parse_path_runs_against_a_deadline(subscription: Subscription, monkeypatch: pytest.MonkeyPatch) -> None:
    subscription.poll()
    monkeypatch.setattr(ics_backend, "_PARSE_SECONDS", -1.0)
    backend = IcsCalendarBackend(CalendarSource.objects.sudo(reason="tests.ics").get(pk=subscription.source.pk))

    # An identity read before any extraction knows no sync deadline: it gets the parse budget.
    with system_context(reason="tests.ics"), pytest.raises(IcsError, match="took too long"):
        backend.read_keys(subscription.stream(), ["launch@example.test"])


def test_a_feed_body_that_names_a_server_file_is_never_read_from_disk(
    subscription: Subscription, tmp_path: Path
) -> None:
    local = tmp_path / "local.ics"
    local.write_text(_feed(LAUNCH))
    subscription.feed.body = str(local)

    with pytest.raises(BridgeSyncError) as refused:
        subscription.poll()

    assert "not valid iCalendar" in str(refused.value)


def test_an_open_series_near_the_last_date_never_fails_a_page(subscription: Subscription) -> None:
    subscription.poll()
    with system_context(reason="tests.ics"):
        Calendar.objects.filter(pk=subscription.calendar.pk).update(overlap_policy="refuse")
    edge = """BEGIN:VEVENT
UID:edge@example.test
DTSTART:99980101T090000Z
DTEND:99980101T100000Z
RRULE:FREQ=DAILY
SUMMARY:Edge
END:VEVENT"""
    subscription.feed.body = _feed(STANDUP, MOVED, HOLIDAY, BERLIN_EVENT, LAUNCH, edge)

    subscription.poll()

    # It lands, or is quarantined on its own; the page never fails.
    assert set(subscription.failures()) <= {"edge@example.test"}
    assert "launch@example.test" in subscription.events()


def test_a_bounded_master_with_a_stale_exdate_past_its_reach_lands(subscription: Subscription) -> None:
    stale = STANDUP.replace(
        "EXDATE;TZID=America/New_York:20260316T090000", "EXDATE;TZID=America/New_York:20260316T090000,23000101T090000"
    )
    subscription.feed.body = _feed(stale, MOVED, HOLIDAY, BERLIN_EVENT, LAUNCH)

    subscription.poll()

    assert subscription.failures() == {}
    assert "standup@example.test" in subscription.events()


def _parsed_zone(rule: str, *, start: str = "19700101T000000") -> Any:
    [zone] = icalendar.Calendar.from_ical(_zone("Probe", rule, start=start).encode()).walk("VTIMEZONE")
    return zone


def test_a_feed_zone_observance_is_counted_in_bounded_time(subscription: Subscription) -> None:
    # A rule whose day filters never match is refused through the recurrence owner, at once.
    began = perf_counter()
    assert ics_backend._transitions(_parsed_zone("FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=30"), 5_000) > 5_000
    assert perf_counter() - began < 0.5
    # A sparse rule counts its true transitions: the leap days from 1970 to 2038.
    assert ics_backend._transitions(_parsed_zone("FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=29"), 5_000) == 17
    # A flood of observances reaches the cap: each counts at least once.
    flood = icalendar.Timezone({"TZID": "Flood"})
    for _index in range(5_001):
        flood.add_component(icalendar.TimezoneStandard({"DTSTART": datetime(1970, 1, 1)}))
    assert ics_backend._transitions(flood, 5_000) > 5_000


def test_each_parse_path_stops_at_the_sync_deadline(subscription: Subscription) -> None:
    subscription.poll()
    subscription.feed.etags = False
    past = monotonic() - 1

    # The feed parse, on extraction, reads the deadline the running sync holds.
    backend = IcsCalendarBackend(CalendarSource.objects.sudo(reason="tests.ics").get(pk=subscription.source.pk))
    with system_context(reason="tests.ics"), sync_deadline_context(past):
        with pytest.raises(IcsError, match="took too long"):
            advance_stream(subscription.stream(), backend)
    # Zone resolution, observance by observance.
    feed = ics_backend._read(_custom_zone_feed("-0500", "-0400").encode())
    with pytest.raises(IcsError, match="took too long"):
        ics_backend._Zones(feed, deadline=past).resolve("Custom Zone")
    # An identity read reads the same one; outside the sync there is none.
    reader = IcsCalendarBackend(CalendarSource.objects.sudo(reason="tests.ics").get(pk=subscription.source.pk))
    with system_context(reason="tests.ics"), sync_deadline_context(past):
        with pytest.raises(IcsError, match="took too long"):
            read_stream_keys(reader, subscription.stream(), ["launch@example.test"])
    assert sync_deadline() is None


def test_a_direct_driver_hands_its_deadline_to_the_parse(subscription: Subscription) -> None:
    subscription.poll()
    subscription.feed.etags = False
    past = monotonic() - 1

    # A caller outside a bridge sync passes the deadline to the driver; the feed parse reads it.
    backend = IcsCalendarBackend(CalendarSource.objects.sudo(reason="tests.ics").get(pk=subscription.source.pk))
    with pytest.raises(IcsError, match="took too long"):
        advance_stream(subscription.stream(), backend, deadline=past)
    assert sync_deadline() is None
    # A driver given none keeps the sync's own.
    backend = IcsCalendarBackend(CalendarSource.objects.sudo(reason="tests.ics").get(pk=subscription.source.pk))
    with system_context(reason="tests.ics"), sync_deadline_context(past):
        with pytest.raises(IcsError, match="took too long"):
            advance_stream(subscription.stream(), backend)
        assert sync_deadline() == past


def test_a_bridge_sync_hands_its_deadline_to_every_partition(
    subscription: Subscription, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[float | None] = []
    events_now = IcsCalendarBackend._events_now

    def watched(self: IcsCalendarBackend, stream: Any) -> Any:
        seen.append(sync_deadline())
        return events_now(self, stream)

    monkeypatch.setattr(IcsCalendarBackend, "_events_now", watched)
    subscription.poll()
    subscription.sweep()

    # The sweep's enumeration read the deadline the sync set once for its cycle.
    assert seen and None not in seen
    assert sync_deadline() is None


def test_a_year_one_recurrence_id_is_one_records_failure_never_a_failed_page(subscription: Subscription) -> None:
    ancient = """BEGIN:VEVENT
UID:ancient@example.test
DTSTART:00010301T090000Z
DTEND:00010301T100000Z
RRULE:FREQ=YEARLY
SUMMARY:Ancient
END:VEVENT"""
    # Not an instance: the yearly series' next one is 0002-03-01, read from the period before it.
    stray = """BEGIN:VEVENT
UID:ancient@example.test
RECURRENCE-ID:00010601T090000Z
DTSTART:00010601T110000Z
DTEND:00010601T120000Z
SUMMARY:Stray
END:VEVENT"""
    subscription.feed.body = _feed(STANDUP, MOVED, HOLIDAY, BERLIN_EVENT, LAUNCH, ancient, stray)

    subscription.poll()

    assert subscription.failures() == {"ancient@example.test#00010601T090000Z": ["invalid_record"]}
    assert {"ancient@example.test", "launch@example.test"} <= set(subscription.events())


def test_a_master_with_a_far_until_lands(subscription: Subscription) -> None:
    long_run = """BEGIN:VEVENT
UID:long-run@example.test
DTSTART:20260406T080000Z
DTEND:20260406T083000Z
RRULE:FREQ=DAILY;UNTIL=20991231T000000Z
SUMMARY:Long run
END:VEVENT"""
    subscription.feed.body = _feed(STANDUP, MOVED, HOLIDAY, BERLIN_EVENT, LAUNCH, long_run)

    subscription.poll()

    # 27,000 days in all: its end is read back from UNTIL, and only a horizon ahead is counted.
    assert subscription.failures() == {}
    assert subscription.events()["long-run@example.test"].series_end == _utc(2099, 12, 30, 8, 30)


def test_a_master_naming_more_exdates_than_a_feed_holds_is_refused_unmapped(subscription: Subscription) -> None:
    first = datetime(2026, 4, 7, 8)
    excluded = ",".join(f"{first + timedelta(days=day):%Y%m%dT%H%M%S}Z" for day in range(10_001))
    crowded = f"""BEGIN:VEVENT
UID:crowded@example.test
DTSTART:20260406T080000Z
DTEND:20260406T083000Z
RRULE:FREQ=DAILY
EXDATE:{excluded}
SUMMARY:Crowded
END:VEVENT"""
    subscription.feed.body = _feed(STANDUP, MOVED, HOLIDAY, BERLIN_EVENT, LAUNCH, crowded)

    began = perf_counter()
    subscription.poll()

    assert subscription.failures() == {"crowded@example.test": ["invalid_event"]}
    assert "launch@example.test" in subscription.events()
    assert perf_counter() - began < 5


def test_an_exdate_list_stops_at_the_sync_deadline() -> None:
    feed = ics_backend._read(_feed(STANDUP).encode())
    [standup] = feed.walk("VEVENT")
    zones = ics_backend._Zones(feed)

    # Its master maps; its EXDATE is checked against the deadline before it is judged.
    with pytest.raises(IcsError, match="took too long"):
        ics_backend._events(
            standup, uid="standup@example.test", zones=zones, zone="America/New_York", feed_zone="America/New_York",
            calendar_zone="UTC", master=standup, deadline=monotonic() - 1,
        )


def test_many_rules_on_one_zone_observance_stop_at_the_cap_fast() -> None:
    repeated = _parsed_zone("\nRRULE:".join(["FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU"] * 200), start="16010101T000000")

    began = perf_counter()
    # 438 transitions a rule from 1601 to 2038: what the cap leaves shrinks rule by rule, so twelve are read.
    assert 5_000 < ics_backend._transitions(repeated, 5_000) <= 5_000 + 438
    assert perf_counter() - began < 1
    with pytest.raises(IcsError, match="took too long"):
        ics_backend._transitions(repeated, 5_000, monotonic() - 1)


def test_a_zone_rule_is_probed_from_its_own_start_and_its_rdates_count() -> None:
    # The fifth Sunday of February that is also its first never meets: an ordinal rule the day probe cannot read.
    never = ics_backend._transitions(_parsed_zone("FREQ=YEARLY;BYMONTH=2;BYDAY=5SU;BYMONTHDAY=1"), 5_000)
    assert never > 5_000
    # A ruled observance's RDATEs count beside its rule's transitions: 2030 to 2038, and two more.
    ruled = _parsed_zone("FREQ=YEARLY\nRDATE:20310601T000000,20320601T000000", start="20300101T000000")
    assert ics_backend._transitions(ruled, 5_000) == 9 + 2


@pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required.")
def test_two_purges_racing_on_a_shared_credential_retire_it_exactly_once(
    subscription: Subscription, monkeypatch: pytest.MonkeyPatch
) -> None:
    subscription.poll()
    monkeypatch.setattr(celery_app, "send_task", lambda name, **options: None)
    twin = subscribe_calendar(subscription.source.owner, name="Twin", url="webcal://feeds.example.test/team.ics")
    _poll(twin)
    credential_id = twin.credential_id
    with system_context(reason="tests.ics"):
        for pk in (subscription.source.pk, twin.pk):
            CalendarSource.objects.filter(pk=pk).update(lifecycle="disconnected")

    def contend() -> int:
        with system_context(reason="tests.ics.contender"):
            return CalendarSource.objects.get(pk=twin.pk).purge()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with system_context(reason="tests.ics.holder"), transaction.atomic():
            # The first purge holds the credential row until this block commits.
            CalendarSource.objects.get(pk=subscription.source.pk).purge()
            contender, pid = submit(pool, contend)
            wait_for_lock(pid, contender)
            assert Credential.objects.filter(pk=credential_id).exists()
        contender.result(timeout=10)

    # The second purge waited, saw both detaches, and retired it.
    with system_context(reason="tests.ics"):
        assert not Credential.objects.filter(pk=credential_id).exists()


def test_an_old_open_series_is_read_by_its_window_not_its_history(subscription: Subscription) -> None:
    subscription.poll()
    with system_context(reason="tests.ics"):
        # Stored before the save-time cap held it, as a legacy row would be.
        _make_unexpandable(
            subscription.events()["berlin@example.test"],
            start=datetime(1995, 1, 1, 9, tzinfo=UTC),
            end=datetime(1995, 1, 1, 10, tzinfo=UTC),
            recurrence="FREQ=DAILY",
        )
        shown = CalendarEvent.objects.filter(calendar=subscription.calendar).occurrences(
            _utc(2026, 3, 1), _utc(2026, 3, 8)
        )

    # R-R3: thirty years of history cost nothing; the week shows its seven days.
    assert len([occurrence for occurrence in shown if occurrence.title == "Berlin sync"]) == 7


def test_the_ics_backend_admits_only_a_static_token(subscription: Subscription) -> None:
    owner = subscription.source.owner
    with system_context(reason="tests.ics"):
        source = CalendarSource.objects.get(pk=subscription.source.pk)
        grant = Credential.objects.create_local_credential(
            owner, kind="basic_auth", name="Not a feed", material={"username": "a", "password": "b"}
        )
        with pytest.raises(ValidationError, match="static token"):
            source.admit_credential(grant)
        source.admit_credential(source.credential)


def test_a_credential_an_account_holds_survives_the_last_detach(
    subscription: Subscription, monkeypatch: pytest.MonkeyPatch
) -> None:
    subscription.poll()
    monkeypatch.setattr(celery_app, "send_task", lambda name, **options: None)
    with system_context(reason="tests.ics"):
        credential = CalendarSource.objects.get(pk=subscription.source.pk).credential
        assert credential is not None
        provider = OAuthClient.objects.create(
            slug="borrowed", display_name="Borrowed", client_id="borrowed-cid",
            authorize_endpoint="https://provider.example/auth", token_endpoint="https://provider.example/token",
        )
        ExternalAccount.objects.create(
            oauth_client=provider, owner=subscription.source.owner, external_id="borrowed", credential=credential
        )
        CalendarSource.objects.get(pk=subscription.source.pk).unsubscribe()

    assert purge_unsubscribed_sources() == 6

    # Only the account's own disconnect retires it; the purge only detached it.
    with system_context(reason="tests.ics"):
        assert Credential.objects.filter(pk=credential.pk).exists()
        assert CalendarSource.objects.get(pk=subscription.source.pk).credential_id is None


# --- poison input --------------------------------------------------------------


def test_nul_characters_long_uids_and_unreadable_instances_never_fail_the_page(subscription: Subscription) -> None:
    subscription.poll()
    long_uid = "x" * 600 + "@example.test"
    noisy = """BEGIN:VEVENT
UID:{uid}
DTSTART:20260402T{hour}0000Z
DTEND:20260402T{hour}3000Z
SUMMARY:Null\x00Title
ORGANIZER;CN=Ali\x00ce:mailto:alice@example.test
END:VEVENT"""
    broken_instance = """BEGIN:VEVENT
UID:standup@example.test
RECURRENCE-ID;TZID=America/New_York:20260323T090000
DTSTART;TZID=America/New_York:not-a-date
SUMMARY:Broken
END:VEVENT"""
    far = """BEGIN:VEVENT
UID:far@example.test
DTSTART:99991228T220000Z
DTEND:99991228T230000Z
SUMMARY:Far
END:VEVENT"""
    with system_context(reason="tests.ics"):
        Calendar.objects.filter(pk=subscription.calendar.pk).update(overlap_policy="refuse")
    subscription.feed.body = _feed(
        STANDUP, MOVED, HOLIDAY, BERLIN_EVENT, LAUNCH, noisy.format(uid="nul@example.test", hour=12),
        noisy.format(uid=long_uid, hour=14), broken_instance, far,
    )

    subscription.poll()

    events = subscription.events()
    digest = "sha256:" + hashlib.sha256(long_uid.encode()).hexdigest()
    assert events["nul@example.test"].title == "NullTitle"
    assert events[digest].title == "NullTitle"
    assert "far@example.test" in events
    # The unreadable instance is quarantined under its own key; its master is untouched.
    assert subscription.failures() == {"standup@example.test#20260323T130000Z": ["invalid_event"]}
    assert "standup@example.test" in events


# --- replicas ------------------------------------------------------------------


def test_a_replica_refuses_every_local_write_and_a_local_calendar_takes_them(
    subscription: Subscription,
) -> None:
    subscription.poll()
    launch = subscription.events()["launch@example.test"]

    with system_context(reason="tests.ics"):
        launch.title = "Edited locally"
        with pytest.raises(ValidationError, match=READ_ONLY_CALENDAR):
            launch.full_clean()
        # The save path refuses too, so no caller can skip the form's clean.
        with pytest.raises(ValidationError, match=READ_ONLY_CALENDAR):
            launch.save()
        launch.refresh_from_db()
        with pytest.raises(ValidationError, match=READ_ONLY_CALENDAR):
            launch.reschedule(_utc(2026, 4, 2, 12), _utc(2026, 4, 2, 13))
        with pytest.raises(ValidationError, match=READ_ONLY_CALENDAR):
            CalendarEvent(calendar=subscription.calendar, title="New", start=launch.start, end=launch.end).save()
        # A linked replica's delete is refused first by integrate's link retention; the
        # calendar names its own refusal in the delete preview.
        assert launch.delete_blocker() == READ_ONLY_CALENDAR
        with pytest.raises(ProtectedError, match="RecordLink rows still refer"):
            launch.delete()
        handle = Handle.objects.upsert(platform="email", value="dave@example.test")
        with pytest.raises(ValidationError, match=READ_ONLY_CALENDAR):
            EventAttendee(event=launch, handle=handle).save()
        organizer = EventAttendee.objects.get(event__title="Standup", role=AttendeeRole.ORGANIZER)
        organizer.response = AttendeeResponse.DECLINED
        with pytest.raises(ValidationError, match=READ_ONLY_CALENDAR):
            organizer.save()
        # No link retains an attendee: the calendar's own delete receiver refuses it.
        with pytest.raises(ValidationError, match=READ_ONLY_CALENDAR):
            EventAttendee.objects.get(pk=organizer.pk).delete()
        # The source's calendars-stream link retains its calendar; the calendar names the refusal in the preview.
        assert subscription.calendar.delete_blocker() == SOURCE_CALENDAR_DELETE
        with pytest.raises(ProtectedError):
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


_WRITABLE_CALENDARS = "query { calendars(where: {writable: {_eq: true}}) { id } }"
_ENSURE_PERSONAL = "mutation { ensure_personal_calendar { ok id } }"


def test_a_read_only_subscription_is_never_offered_for_new_events_nor_taken_as_personal(
    subscription: Subscription,
) -> None:
    """The event form's ``writable`` calendars and a user's own calendar both take local edits.

    The subscriber holds ``write`` on the feed's calendar, yet it refuses local events,
    so the form offers nothing until the user starts their own: a new local calendar.
    The query rule and :attr:`Calendar.is_writable` agree on every row.
    """

    subscription.poll()
    feed_calendar = subscription.calendar
    with system_context(reason="tests.ics"):
        user = get_user_model().objects.get(pk=subscription.source.owner_id)
    console = addon_schema(calendar_schema.schemas, "console")

    def writable() -> list[str]:
        rows = result_data(execute_schema(console, _WRITABLE_CALENDARS, {}, user=user))["calendars"]
        return [row["id"] for row in rows]

    assert Calendar.objects.with_actor(user).with_action("write").filter(pk=feed_calendar.pk).exists()
    assert feed_calendar.is_writable is False
    assert writable() == []

    own = result_data(execute_schema(console, _ENSURE_PERSONAL, {}, user=user))["ensure_personal_calendar"]

    assert own["ok"] is True and own["id"] != feed_calendar.sqid
    assert writable() == [own["id"]]
    with system_context(reason="tests.ics"):
        rows = list(Calendar.objects.all())
        taking = set(Calendar.objects.taking_local_edits().values_list("pk", flat=True))
    assert {row.pk for row in rows if row.is_writable} == taking
    assert len(rows) == 2 and feed_calendar.pk not in taking


def test_a_handle_merge_folds_two_attendees_of_one_replica(subscription: Subscription) -> None:
    subscription.poll()
    master = subscription.events()["standup@example.test"]

    with system_context(reason="tests.ics"):
        bob = Handle.objects.get(normalized_value="bob@example.test")
        carol = Handle.objects.get(normalized_value="carol@example.test")
        bob.fold([carol])
        rows = list(EventAttendee.objects.filter(event=master, handle__in=[bob, carol]).values_list("handle", "role"))

    assert rows == [(bob.pk, AttendeeRole.REQUIRED)]


def test_a_source_moving_a_series_resends_its_instances_under_new_keys(subscription: Subscription) -> None:
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
    old_keys = ("standup@example.test#20260309T130000Z", "standup@example.test#20260316T130000Z")
    # The new keys land at once; the old ones go when the absence sweep tombstones them.
    assert {"standup@example.test#20260309T140000Z", *old_keys} <= set(subscription.events())
    subscription.sweep()
    subscription.sweep()

    events = subscription.events()
    master = events["standup@example.test"]
    with system_context(reason="tests.ics"):
        overrides = sorted(CalendarEvent.objects.filter(series=master).values_list("original_start", "status"))
        old = RecordLink.objects.get(stream=subscription.stream(), external_key="standup@example.test#20260309T130000Z")
        series = CalendarEvent.objects.filter(calendar=subscription.calendar, title__startswith="Standup")
        starts = [o.start for o in series.occurrences(_utc(2026, 3, 1), _utc(2026, 4, 1))]
    assert subscription.failures() == {}
    assert old.status == LinkStatus.TOMBSTONE
    # Each instance key owns its placement: the old rows went with their keys, new rows came with the new ones.
    assert overrides == [
        (_utc(2026, 3, 9, 10, zone=NEW_YORK), "confirmed"),
        (_utc(2026, 3, 16, 10, zone=NEW_YORK), "cancelled"),
    ]
    assert not CalendarEvent.system_queryset().filter(pk__in=[moved.pk, excluded.pk]).exists()
    assert starts == [
        _utc(2026, 3, 2, 10, zone=NEW_YORK),
        _utc(2026, 3, 9, 11, zone=NEW_YORK),
        _utc(2026, 3, 23, 10, zone=NEW_YORK),
    ]


def test_a_source_shortening_a_series_with_an_exdate_keeps_the_page(subscription: Subscription) -> None:
    subscription.poll()

    subscription.feed.body = _feed(STANDUP.replace("COUNT=4", "COUNT=2"), MOVED, HOLIDAY, BERLIN_EVENT, LAUNCH)
    subscription.poll()

    with system_context(reason="tests.ics"):
        series = CalendarEvent.objects.filter(calendar=subscription.calendar, title__startswith="Standup")
        starts = [o.start for o in series.occurrences(_utc(2026, 3, 1), _utc(2026, 4, 1))]
    assert subscription.failures() == {}
    assert subscription.events()["standup@example.test"].recurrence == "FREQ=WEEKLY;COUNT=2;BYDAY=MO"
    assert starts == [_utc(2026, 3, 2, 9, zone=NEW_YORK), _utc(2026, 3, 9, 10, zone=NEW_YORK)]
    # The 16 March EXDATE is no instance of the shortened rule: the feed parse drops it, and the sweep its row.
    excluded = "standup@example.test#20260316T130000Z"
    subscription.sweep()
    subscription.sweep()
    assert subscription.statuses(excluded) == {excluded: LinkStatus.TOMBSTONE}
    assert excluded not in subscription.events()


def test_a_zone_change_on_a_subscribed_calendar_resyncs_instead_of_moving_replicas(subscription: Subscription) -> None:
    subscription.poll()
    before = _snapshot(subscription)
    generation = subscription.stream().generation
    owner = subscription.source.owner

    with actor_context(owner):
        mine = Calendar.objects.with_actor(owner).get(pk=subscription.calendar.pk)
        mine.timezone = "Europe/Berlin"
        mine.save()

    assert subscription.stream().resync_required is True
    assert _snapshot(subscription) == before
    subscription.poll()
    assert subscription.stream().generation == generation + 1
    assert subscription.failures() == {}


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
