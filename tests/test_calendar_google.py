"""Google Calendar sync-in through the real connect verb, pinned client, stream driver and calendar managers.

A fake Google Calendar API answers through integrate's ``mock_transport``: each
account (by its access token) has a calendar list and events, and every change
bumps a version, so a sync token lists only what changed since it and an expired
token answers ``410 Gone``. Nothing patches the backend.
"""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, tzinfo
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit
from zoneinfo import ZoneInfo

import httpx2
import pytest
from django.core.cache import caches
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import Q
from rebac import actor_context, system_context

from angee.calendar import schema as calendar_schema
from angee.calendar.backends import ParsedAttendee, organizer_first
from angee.calendar.models import AttendeeResponse, AttendeeRole
from angee.calendar.tasks import purge_unsubscribed_sources
from angee.calendar.testing.models import CalendarEvent, CalendarSource, EventAttendee
from angee.calendar_integrate_google.backend import CALENDAR_READONLY
from angee.calendar_integrate_google.connect import connect_google_calendar
from angee.graphql.testing.documents import execute_schema, result_data
from angee.integrate.credentials import CredentialKind
from angee.integrate.states import LinkStatus, StreamState
from angee.integrate.streams import advance_stream, sync_bridge
from angee.integrate.testing.http import mock_transport
from angee.integrate.testing.models import RecordLink, SyncStream
from angee.jobs.celery import app as celery_app
from tests.conftest import Credential, ExternalAccount, OAuthClient, Vendor, addon_schema, create_user
from tests.lock_waits import submit, wait_for_lock

pytestmark = pytest.mark.django_db(transaction=True)

NEW_YORK = ZoneInfo("America/New_York")
BERLIN = ZoneInfo("Europe/Berlin")
API = "/calendar/v3/"


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0, *, zone: tzinfo = UTC) -> datetime:
    """Build a wall-clock moment in ``zone`` as aware UTC."""

    return datetime(year, month, day, hour, minute, tzinfo=zone).astimezone(UTC)


@dataclass
class Account:
    """One Google account's calendar list and events, each event stamped with the version that last changed it."""

    calendars: dict[str, dict[str, Any]] = field(default_factory=dict)
    events: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)


@dataclass
class FakeGoogle:
    """Google Calendar v3 for several accounts, told apart by their bearer tokens."""

    accounts: dict[str, Account] = field(default_factory=dict)
    version: int = 0
    expired: set[str] = field(default_factory=set)
    refused_pages: set[str] = field(default_factory=set)
    """Page tokens Google answers once with ``400 invalid``, as it does a page token it no longer accepts."""
    requests: list[httpx2.Request] = field(default_factory=list)

    def put(self, token: str, calendar_id: str, event: dict[str, Any]) -> None:
        """Create or change one event of ``calendar_id``, as a remote edit does."""

        self.version += 1
        events = self.accounts[token].events.setdefault(calendar_id, {})
        events[event["id"]] = {**event, "_version": self.version}

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        account = self.accounts.get(token)
        if account is None:
            return httpx2.Response(401, json={"error": {"code": 401, "status": "UNAUTHENTICATED"}})
        url = urlsplit(str(request.url))
        params = {key: values[0] for key, values in parse_qs(url.query).items()}
        parts = [unquote(part) for part in url.path.removeprefix(API).split("/")]
        if parts == ["users", "me", "calendarList"]:
            return httpx2.Response(200, json={"items": list(account.calendars.values())})
        if parts[1] not in account.calendars and parts[1] not in account.events:
            # A calendar the account no longer reads: what a read of one missing event's calendar learns.
            return httpx2.Response(404)
        events = account.events.get(parts[1], {})
        if parts[2:] == ["events"]:
            return self._list(events, params)
        event = events.get(parts[3])
        if len(parts) == 4:
            return httpx2.Response(200, json=_public(event)) if event else httpx2.Response(404)
        found = [
            row for row in events.values()
            if row.get("recurringEventId") == parts[3] and _original(row) == params["originalStart"]
        ]
        return httpx2.Response(200, json={"items": [_public(row) for row in found]})

    def _list(self, events: dict[str, dict[str, Any]], params: dict[str, str]) -> httpx2.Response:
        token = params.get("syncToken", "")
        if params.get("pageToken") in self.refused_pages:
            self.refused_pages.discard(params["pageToken"])
            return httpx2.Response(400, json={"error": {"code": 400, "errors": [{"reason": "invalid"}]}})
        if token in self.expired:
            return httpx2.Response(410, json={"error": {"code": 410, "errors": [{"reason": "fullSyncRequired"}]}})
        since = int(token) if token else 0
        rows = sorted((row for row in events.values() if row["_version"] > since), key=lambda row: row["id"])
        start, size = int(params.get("pageToken") or 0), int(params["maxResults"])
        page: dict[str, Any] = {"items": [_public(row) for row in rows[start : start + size]]}
        if start + size < len(rows):
            page["nextPageToken"] = str(start + size)
        else:
            page["nextSyncToken"] = str(self.version)
        return httpx2.Response(200, json=page)


def _public(row: dict[str, Any] | None) -> dict[str, Any]:
    return {key: value for key, value in (row or {}).items() if not key.startswith("_")}


def _original(row: dict[str, Any]) -> str:
    original = row["originalStartTime"]
    if "date" in original:
        return original["date"]
    return datetime.fromisoformat(original["dateTime"]).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


STANDUP = {
    "id": "standup",
    "status": "confirmed",
    "summary": "Standup",
    "start": {"dateTime": "2026-03-02T09:00:00-05:00", "timeZone": "America/New_York"},
    "end": {"dateTime": "2026-03-02T09:30:00-05:00", "timeZone": "America/New_York"},
    "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=5", "EXDATE;TZID=America/New_York:20260316T090000"],
    "organizer": {"email": "alice@example.test", "displayName": "Alice"},
    "attendees": [
        {"email": "alice@example.test", "organizer": True, "responseStatus": "accepted"},
        {"email": "bob@example.test", "displayName": "Bob", "responseStatus": "accepted"},
        {"email": "Carol@Example.test", "optional": True, "responseStatus": "tentative"},
        {"email": "room@resource.example.test", "resource": True, "responseStatus": "accepted"},
    ],
    "conferenceData": {"entryPoints": [{"entryPointType": "video", "uri": "https://meet.example.test/standup"}]},
    "visibility": "private",
}
MOVED = {
    "id": "standup_20260309T140000Z",
    "status": "confirmed",
    "recurringEventId": "standup",
    "originalStartTime": {"dateTime": "2026-03-09T09:00:00-04:00", "timeZone": "America/New_York"},
    "summary": "Standup (moved)",
    "start": {"dateTime": "2026-03-09T10:00:00-04:00", "timeZone": "America/New_York"},
    "end": {"dateTime": "2026-03-09T10:45:00-04:00", "timeZone": "America/New_York"},
}
CANCELLED_INSTANCE = {
    "id": "standup_20260323T130000Z",
    "status": "cancelled",
    "recurringEventId": "standup",
    "originalStartTime": {"dateTime": "2026-03-23T09:00:00-04:00", "timeZone": "America/New_York"},
}
BERLIN_WEEKLY = {
    "id": "berlin",
    "status": "tentative",
    "summary": "Berlin sync",
    "start": {"dateTime": "2026-03-23T09:00:00+01:00", "timeZone": "Europe/Berlin"},
    "end": {"dateTime": "2026-03-23T10:00:00+01:00", "timeZone": "Europe/Berlin"},
    "recurrence": ["RRULE:FREQ=WEEKLY;COUNT=2"],
    "transparency": "transparent",
}
HOLIDAY = {
    "id": "holiday",
    "status": "confirmed",
    "summary": "Holiday",
    "start": {"date": "2026-03-08"},
    "end": {"date": "2026-03-09"},
}
GONE_LONG_AGO = {"id": "gone", "status": "cancelled"}


@dataclass
class Connected:
    """A user with Google accounts connected as calendar sources, and the fake Google answering them."""

    user: Any
    google: FakeGoogle
    client: Any
    sources: dict[str, Any] = field(default_factory=dict)

    def poll(self, token: str = "work-token") -> int:
        with system_context(reason="tests.google.poll"):
            return sync_bridge(CalendarSource.objects.get(pk=self.sources[token].pk))

    def stream(self, key: str = "events", *, token: str = "work-token", partition: str = "work@example.test") -> Any:
        rows = SyncStream.objects.sudo(reason="tests.google").filter(
            integration_id=self.sources[token].pk, key=key, partition="" if key == "calendars" else partition
        )
        return rows.latest("generation")

    def events(self, *, token: str = "work-token", partition: str = "work@example.test") -> dict[str, Any]:
        links = RecordLink.objects.sudo(reason="tests.google").filter(
            stream=self.stream(token=token, partition=partition), target_object_id__isnull=False
        )
        rows = {str(row.pk): row for row in CalendarEvent.system_queryset()}
        return {link.external_key: rows[link.target_object_id] for link in links if link.target_object_id in rows}

    def source(self, token: str = "work-token") -> Any:
        with system_context(reason="tests.google"):
            return CalendarSource.objects.get(pk=self.sources[token].pk)

    def listings(self) -> list[dict[str, str]]:
        """Return the query of every ``events.list`` request so far."""

        return [
            {key: values[0] for key, values in parse_qs(urlsplit(str(request.url)).query).items()}
            for request in self.google.requests
            if urlsplit(str(request.url)).path.endswith("/events")
        ]


def _connect(user: Any, credential: Any) -> Any:
    """Connect ``credential`` as ``user`` does through the GraphQL verb: the user is the actor."""

    with actor_context(user):
        return connect_google_calendar(user, credential)


def _account(client: Any, user: Any, external_id: str, token: str, scopes: str = CALENDAR_READONLY) -> Any:
    """Link a Google account to ``user`` through integrate's connect verb; return its credential."""

    account = ExternalAccount.objects.connect_oauth(
        client,
        user=user,
        external_id=external_id,
        tokens={"access_token": token, "token_type": "Bearer", "scope": f"openid email {scopes}"},
        claims={"sub": external_id, "email": f"{external_id}@example.test"},
    )
    return account.credential


@pytest.fixture
def connected() -> Iterator[Connected]:
    caches["default"].clear()
    google = FakeGoogle()
    google.accounts["work-token"] = Account(
        calendars={
            "work@example.test": {
                "id": "work@example.test",
                "summary": "Work",
                "backgroundColor": "#9fe1e7",
                "timeZone": "America/New_York",
            },
            "team#holidays@group.calendar.google.com": {
                "id": "team#holidays@group.calendar.google.com", "summary": "Holidays",
                "summaryOverride": "Team holidays", "timeZone": "Europe/Berlin",
            },
        }
    )
    google.accounts["home-token"] = Account(
        calendars={"home@example.test": {"id": "home@example.test", "summary": "Home", "timeZone": "Europe/Berlin"}}
    )
    for event in (STANDUP, MOVED, CANCELLED_INSTANCE, BERLIN_WEEKLY, HOLIDAY, GONE_LONG_AGO):
        google.put("work-token", "work@example.test", event)
    with mock_transport(google):
        user = create_user("google-user")
        with system_context(reason="tests.google.setup"):
            Vendor._base_manager.get_or_create(slug="google", defaults={"display_name": "Google"})
            client = OAuthClient.objects.create(
                slug="google",
                display_name="Google",
                client_id="google-cid",
                authorize_endpoint="https://accounts.example.test/auth",
                token_endpoint="https://accounts.example.test/token",
            )
        state = Connected(user, google, client)
        for token, external_id in (("work-token", "work"), ("home-token", "home")):
            credential = _account(client, user, external_id, token)
            state.sources[token] = _connect(user, credential)
        yield state


def test_two_accounts_are_two_sources_each_discovering_its_calendars(connected: Connected) -> None:
    connected.poll("work-token")
    connected.poll("home-token")

    with system_context(reason="tests.google"):
        work = CalendarSource.objects.get(pk=connected.sources["work-token"].pk)
        home = CalendarSource.objects.get(pk=connected.sources["home-token"].pk)
        assert (work.backend_class, work.display_name, home.display_name) == (
            "google", "work@example.test", "home@example.test"
        )
        assert {(c.name, c.color, c.timezone) for c in work.calendars.all()} == {
            ("Work", "#9fe1e7", "America/New_York"),
            ("Team holidays", "", "Europe/Berlin"),
        }
        assert [c.name for c in home.calendars.all()] == ["Home"]
        # Each events stream is held by its calendar's link: the container seam, no interim lookup.
        links = {link.external_key: link for link in RecordLink.objects.filter(stream=connected.stream("calendars"))}
        events = SyncStream.objects.filter(integration_id=work.pk, key="events")
        assert {(stream.partition, stream.container_id) for stream in events} == {
            (key, link.pk) for key, link in links.items()
        }
        assert connected.stream("calendars").holds_containers
    # The first sync declared each calendar's events stream in the same run, after its calendar.
    assert {"Standup", "Berlin sync", "Holiday"} <= {event.title for event in connected.events().values()}


def test_an_account_already_synced_is_refused_and_only_calendar_grants_attach(connected: Connected) -> None:
    with system_context(reason="tests.google"):
        work = connected.sources["work-token"].credential
    with pytest.raises(ValidationError, match="already connected"):
        _connect(connected.user, work)

    narrow = _account(connected.client, connected.user, "narrow", "narrow-token", scopes="profile")
    with pytest.raises(ValidationError, match="Calendar access"):
        _connect(connected.user, narrow)
    stranger = _account(connected.client, create_user("stranger"), "stranger", "stranger-token")
    with pytest.raises(ValidationError, match="connected Google accounts"):
        _connect(connected.user, stranger)
    with system_context(reason="tests.google"):
        source = CalendarSource.objects.get(pk=connected.sources["home-token"].pk)
        with pytest.raises(ValidationError, match="Calendar access"):
            source.attach_credential(narrow)


def test_a_source_offers_only_the_users_own_google_grants_with_calendar_access(connected: Connected) -> None:
    """A Google source reads with a ``google`` grant covering ``calendar.readonly`` and offers the user's own.

    Another client's grant, a grant without Calendar access and a key-pair credential
    are neither read with, offered nor admitted; another user's grant is read with but
    never offered. The console query offers the same accounts, before any source exists,
    with the registration and scopes another account connects with.
    """

    with system_context(reason="tests.google"):
        github = OAuthClient.objects.create(slug="github", display_name="GitHub", client_id="github-cid")
    other_client = _account(github, connected.user, "github-user", "github-token")
    narrow = _account(connected.client, connected.user, "narrow", "narrow-token", scopes="profile")
    stranger = create_user("offer-stranger")
    foreign = _account(connected.client, stranger, "offer-stranger", "stranger-token")
    key_pair = _account(connected.client, connected.user, "key-pair", "key-pair-token")
    with system_context(reason="tests.google"):
        # A grant in every other respect: only its kind keeps the source from reading with it.
        Credential.objects.filter(pk=key_pair.pk).update(kind=CredentialKind.JWT_BEARER)
        key_pair.refresh_from_db()
        own = {connected.source(token).credential for token in ("work-token", "home-token")}
        source = CalendarSource.objects.build(backend_class="google")
        offered = set(source.connectable_credentials(connected.user))
        assert offered == own
        assert source.reads_with(foreign)
        assert not any(source.reads_with(credential) for credential in (other_client, narrow, key_pair))
        home = connected.source("home-token")
        for refused in (other_client, narrow, key_pair):
            with pytest.raises(ValidationError, match="Calendar access"):
                home.attach_credential(refused)

    schema = addon_schema(calendar_schema.schemas, "console")
    query = (
        'query { calendar_source_backend(backend_class: "google") '
        "{ key label oauth_client oauth_scopes accounts { id } } }"
    )
    listed = result_data(execute_schema(schema, query, user=connected.user))["calendar_source_backend"]
    assert (listed["key"], listed["label"]) == ("google", "Google Calendar")
    assert listed["oauth_client"] == str(connected.client.public_id)
    assert listed["oauth_scopes"] == [CALENDAR_READONLY]
    assert {account["id"] for account in listed["accounts"]} == {str(credential.public_id) for credential in own}
    theirs = result_data(execute_schema(schema, query, user=stranger))["calendar_source_backend"]
    assert theirs["accounts"] == [{"id": str(foreign.public_id)}]


def test_a_sync_token_replay_changes_nothing_and_a_410_resyncs_once(connected: Connected) -> None:
    connected.poll()
    before = {key: (event.pk, event.updated_at) for key, event in connected.events().items()}
    baseline = connected.listings()
    assert all("syncToken" not in query for query in baseline)
    # The absence sweep reads what its own listing served: no event is fetched one by one,
    # and each calendar is listed once for the sweep, in pages of 250.
    assert not [request for request in connected.google.requests if "/events/" in urlsplit(str(request.url)).path]
    assert len([query for query in baseline if query["maxResults"] == "250"]) == 2

    connected.poll()
    replay = connected.listings()[len(baseline):]
    assert replay and all(query.get("syncToken") == str(connected.google.version) for query in replay)
    assert {key: (event.pk, event.updated_at) for key, event in connected.events().items()} == before

    connected.google.expired.add(str(connected.google.version))
    seen = len(connected.listings())
    connected.poll()
    baselines = [
        query for query in connected.listings()[seen:] if "syncToken" not in query and query["maxResults"] == "100"
    ]
    # One new baseline per calendar whose token expired; the events keep their rows.
    assert len(baselines) == 2
    assert {key: event.pk for key, event in connected.events().items()} == {
        key: pk for key, (pk, _updated) in before.items()
    }
    connected.google.expired.clear()
    connected.google.put("work-token", "work@example.test", {**HOLIDAY, "summary": "Holiday (moved)"})
    connected.poll()
    assert connected.events()["holiday"].title == "Holiday (moved)"


def test_a_series_keeps_its_rule_and_its_moved_and_cancelled_instances(connected: Connected) -> None:
    connected.poll()
    events = connected.events()
    master = events["standup"]
    moved = events["standup#20260309T130000Z"]
    excluded = events["standup#20260316T130000Z"]
    cancelled = events["standup#20260323T130000Z"]

    assert (master.recurrence, master.timezone, master.series_id) == (
        "FREQ=WEEKLY;COUNT=5;BYDAY=MO", "America/New_York", None
    )
    assert (moved.series_id, moved.original_start, moved.title) == (
        master.pk, _utc(2026, 3, 9, 9, zone=NEW_YORK), "Standup (moved)"
    )
    assert (excluded.status, cancelled.status) == ("cancelled", "cancelled")
    assert {excluded.series_id, cancelled.series_id} == {master.pk}
    assert "gone" not in events
    with system_context(reason="tests.google"):
        occurrences = CalendarEvent.objects.filter(Q(pk=master.pk) | Q(series=master))
        starts = [o.start for o in occurrences.occurrences(_utc(2026, 3, 1), _utc(2026, 4, 1))]
    # Monday 09:00 New York on both sides of the spring-forward; the 9th moved, the 16th and 23rd cancelled.
    assert starts == [
        _utc(2026, 3, 2, 9, zone=NEW_YORK),
        _utc(2026, 3, 9, 10, zone=NEW_YORK),
        _utc(2026, 3, 30, 9, zone=NEW_YORK),
    ]


def test_start_time_zone_keeps_the_wall_clock_across_dst(connected: Connected) -> None:
    connected.poll()
    berlin, holiday = connected.events()["berlin"], connected.events()["holiday"]

    assert (berlin.timezone, berlin.status, berlin.transparency) == ("Europe/Berlin", "tentative", "free")
    with system_context(reason="tests.google"):
        rows = CalendarEvent.objects.filter(pk=berlin.pk)
        starts = [o.start for o in rows.occurrences(_utc(2026, 3, 1), _utc(2026, 4, 30))]
    assert starts == [_utc(2026, 3, 23, 9, zone=BERLIN), _utc(2026, 3, 30, 9, zone=BERLIN)]
    assert [moment.hour for moment in starts] == [8, 7]
    # An all-day date is local midnight in the calendar's zone.
    assert (holiday.all_day, holiday.start, holiday.end) == (
        True, _utc(2026, 3, 8, zone=NEW_YORK), _utc(2026, 3, 9, zone=NEW_YORK)
    )


def test_attendees_become_handles_with_one_organizer(connected: Connected) -> None:
    connected.poll()
    master = connected.events()["standup"]

    with system_context(reason="tests.google"):
        rows = {
            row.handle.normalized_value: (row.role, row.response, row.handle.display_name)
            for row in EventAttendee.objects.filter(event=master).select_related("handle")
        }
    assert rows == {
        "alice@example.test": (AttendeeRole.ORGANIZER, AttendeeResponse.ACCEPTED, "Alice"),
        "bob@example.test": (AttendeeRole.REQUIRED, AttendeeResponse.ACCEPTED, "Bob"),
        "carol@example.test": (AttendeeRole.OPTIONAL, AttendeeResponse.TENTATIVE, ""),
    }
    assert (master.conference_url, master.visibility) == ("https://meet.example.test/standup", "private")


def test_a_removed_event_is_cancelled_and_read_back_by_identity(connected: Connected) -> None:
    connected.poll()
    holiday = connected.events()["holiday"]
    connected.google.put("work-token", "work@example.test", {"id": "holiday", "status": "cancelled"})

    connected.poll()
    holiday.refresh_from_db()
    assert holiday.status == "cancelled"
    with system_context(reason="tests.google"):
        backend = CalendarSource.objects.get(pk=connected.sources["work-token"].pk).backend
        payloads = backend.read_events(connected.stream(), ["holiday", "standup#20260309T130000Z", "missing"])
    assert payloads["holiday"] is None and payloads["missing"] is None
    assert payloads["standup#20260309T130000Z"]["event"]["title"] == "Standup (moved)"


MALFORMED = (
    {"id": "no-start", "status": "confirmed", "summary": "No start"},
    {"id": "bad-time", "status": "confirmed", "start": {"dateTime": "soon"}, "end": {"dateTime": "later"}},
    {"id": "typed", "status": "confirmed", "start": "2026-03-02", "recurrence": ["RRULE:FREQ=DAILY"]},
    {
        "id": "bad-rule",
        "status": "confirmed",
        "start": {"dateTime": "2026-03-02T09:00:00Z"},
        "end": {"dateTime": "2026-03-02T10:00:00Z"},
        "recurrence": ["RRULE:FREQ=SOMETIMES"],
    },
)
"""Items Google should never send: no start, an unreadable time, a start that is no object, an unknown rule."""


def test_a_malformed_google_event_is_set_aside_and_the_rest_of_its_page_syncs(connected: Connected) -> None:
    for event in MALFORMED:
        connected.google.put("work-token", "work@example.test", event)

    connected.poll()

    with system_context(reason="tests.google"):
        links = RecordLink.objects.filter(stream=connected.stream(), external_key__in=[row["id"] for row in MALFORMED])
        failures = {link.external_key: [row.code for row in link.failures(open_only=True)] for link in links}
    assert failures == {row["id"]: ["invalid_event"] for row in MALFORMED}
    assert {"standup", "holiday"} <= set(connected.events())


def test_a_hidden_calendar_retires_its_events_and_they_come_back_when_it_returns(connected: Connected) -> None:
    connected.poll()
    standup = connected.events()["standup"]
    work = connected.google.accounts["work-token"].calendars.pop("work@example.test")

    for _sweep in range(2):
        SyncStream.objects.sudo(reason="tests.google").filter(
            integration_id=connected.sources["work-token"].pk
        ).update(last_reconciled_at=None)
        connected.poll()

    with system_context(reason="tests.google"):
        link = RecordLink.objects.get(stream=connected.stream("calendars"), external_key="work@example.test")
        assert link.status == LinkStatus.TOMBSTONE
        assert connected.stream().state == StreamState.RETIRED
        standup.refresh_from_db()
    assert standup.status == "cancelled"

    # The calendar is listed again: its link revives, its events stream reopens and its events re-apply.
    connected.google.accounts["work-token"].calendars["work@example.test"] = work
    connected.poll()
    with system_context(reason="tests.google"):
        link.refresh_from_db()
        assert (link.status, link.tombstoned_at) == (LinkStatus.CURRENT, None)
        assert connected.stream().state == StreamState.OPEN
        standup.refresh_from_db()
        failures = RecordLink.objects.filter(stream__integration_id=connected.sources["work-token"].pk)
        assert not [row.code for link_row in failures for row in link_row.failures(open_only=True)]
    assert standup.status == "confirmed"
    assert connected.events()["standup"].pk == standup.pk


def test_unsubscribing_a_google_source_keeps_the_account_credential_and_revokes_nothing(
    connected: Connected, monkeypatch: pytest.MonkeyPatch
) -> None:
    connected.poll()
    monkeypatch.setattr(celery_app, "send_task", lambda name, **options: None)
    with system_context(reason="tests.google"):
        OAuthClient.objects.filter(pk=connected.client.pk).update(revoke_endpoint="https://accounts.example.test/revoke")
        source = CalendarSource.objects.get(pk=connected.sources["work-token"].pk)
        credential = source.credential
        source.unsubscribe()
    connected.google.requests.clear()

    assert purge_unsubscribed_sources() > 0

    # The account's own disconnect retires its credential; the purge only detached it.
    with system_context(reason="tests.google"):
        assert CalendarSource.objects.get(pk=source.pk).credential_id is None
        assert Credential.objects.filter(pk=credential.pk).exists()
        assert ExternalAccount.objects.filter(credential=credential).exists()
    assert not [request for request in connected.google.requests if request.url.path.endswith("/revoke")]


def test_the_organizer_carries_its_listed_answer_or_accepts() -> None:
    bob = ParsedAttendee("bob@example.test", AttendeeRole.REQUIRED, AttendeeResponse.TENTATIVE, "Bob")
    unlisted = organizer_first([bob], "alice@example.test", name="Alice")
    alice = replace(bob, address="Alice@Example.test")
    listed = organizer_first([bob, alice], "alice@example.test")

    assert unlisted == (
        ParsedAttendee("alice@example.test", AttendeeRole.ORGANIZER, AttendeeResponse.ACCEPTED, "Alice"),
        bob,
    )
    assert listed[0] == ParsedAttendee("alice@example.test", AttendeeRole.ORGANIZER, AttendeeResponse.TENTATIVE, "Bob")
    assert listed[1:] == (bob,)


def test_a_page_with_more_records_than_the_bound_pages_locally(connected: Connected) -> None:
    with system_context(reason="tests.google"):
        CalendarSource.objects.filter(pk=connected.sources["work-token"].pk).update(config={"sync_page_bound": 1})
    connected.poll()
    connected.poll()

    assert {"standup", "standup#20260316T130000Z", "standup#20260309T130000Z", "holiday"} <= set(connected.events())
    assert connected.stream().cursor == {"sync_token": str(connected.google.version)}


def _work_listings(google: FakeGoogle) -> list[dict[str, str]]:
    """Return the query of every ``events.list`` request of the work calendar."""

    return [
        {key: values[0] for key, values in parse_qs(urlsplit(str(request.url)).query).items()}
        for request in google.requests
        if urlsplit(str(request.url)).path == f"{API}calendars/work%40example.test/events"
    ]


SERIES_WITH_TWO_EXDATES = {
    "id": "aaa-retro",
    "status": "confirmed",
    "summary": "Retro",
    "start": {"dateTime": "2026-04-06T15:00:00Z", "timeZone": "UTC"},
    "end": {"dateTime": "2026-04-06T16:00:00Z", "timeZone": "UTC"},
    "recurrence": ["RRULE:FREQ=WEEKLY;COUNT=4", "EXDATE:20260413T150000Z,20260420T150000Z"],
}


def test_a_redelivered_delta_changes_nothing(connected: Connected) -> None:
    connected.poll()
    applied = connected.stream().cursor
    connected.google.put("work-token", "work@example.test", {**HOLIDAY, "summary": "Holiday (moved)"})
    connected.poll()
    before = {key: (event.pk, event.title, event.updated_at) for key, event in connected.events().items()}

    # The stream reads from the token before that change again: Google re-delivers it.
    SyncStream.objects.sudo(reason="tests.google").filter(pk=connected.stream().pk).update(cursor=applied)
    seen = len(_work_listings(connected.google))
    connected.poll()

    assert [query.get("syncToken") for query in _work_listings(connected.google)[seen:]] == [applied["sync_token"]]
    assert {key: (event.pk, event.title, event.updated_at) for key, event in connected.events().items()} == before


def test_the_first_google_page_is_listed_once_while_it_pages_locally(connected: Connected) -> None:
    connected.google.put("work-token", "work@example.test", SERIES_WITH_TWO_EXDATES)
    with system_context(reason="tests.google"):
        CalendarSource.objects.filter(pk=connected.sources["work-token"].pk).update(config={"sync_page_bound": 1})
    connected.poll()

    first = [query for query in _work_listings(connected.google) if query["maxResults"] == "1"][:2]
    # The first page maps to three records, paged one at a time from the page the adapter holds.
    assert [query.get("pageToken") for query in first] == [None, "1"]
    assert {"aaa-retro", "aaa-retro#20260413T150000Z", "aaa-retro#20260420T150000Z"} <= set(connected.events())


def test_a_fresh_adapter_between_local_pages_reads_the_page_again_from_its_first_key(connected: Connected) -> None:
    connected.poll()
    token = connected.stream().cursor["sync_token"]
    connected.google.put("work-token", "work@example.test", SERIES_WITH_TWO_EXDATES)

    result = advance_stream(connected.stream(), connected.source().backend, page_bound=1)
    assert (result.count, result.exhausted, result.stream.cursor["from"]) == (1, False, {"sync_token": token})
    fresh = connected.source().backend
    while not result.exhausted:
        result = advance_stream(result.stream, fresh, page_bound=1)

    assert [query.get("syncToken") for query in _work_listings(connected.google)[-2:]] == [token, token]
    assert {"aaa-retro", "aaa-retro#20260413T150000Z", "aaa-retro#20260420T150000Z"} <= set(connected.events())


def test_a_page_token_google_refuses_lists_again_from_the_sync_token(connected: Connected) -> None:
    connected.poll()
    token = connected.stream().cursor["sync_token"]
    with system_context(reason="tests.google"):
        CalendarSource.objects.filter(pk=connected.sources["work-token"].pk).update(config={"sync_page_bound": 1})
    connected.google.put("work-token", "work@example.test", {**HOLIDAY, "summary": "Holiday (moved)"})
    connected.google.put("work-token", "work@example.test", {**BERLIN_WEEKLY, "summary": "Berlin sync (moved)"})
    connected.google.refused_pages.add("1")
    seen = len(_work_listings(connected.google))

    for _poll in range(2):
        connected.poll()

    restarts = [query for query in _work_listings(connected.google)[seen:] if "pageToken" not in query]
    assert [query.get("syncToken") for query in restarts][:2] == [token, token]
    assert (connected.events()["holiday"].title, connected.events()["berlin"].title) == (
        "Holiday (moved)", "Berlin sync (moved)"
    )


def test_an_exception_on_an_earlier_page_than_its_master_lands_on_its_series(connected: Connected) -> None:
    master = {
        "id": "zz-review",
        "status": "confirmed",
        "summary": "Review",
        "start": {"dateTime": "2026-04-07T15:00:00Z", "timeZone": "UTC"},
        "end": {"dateTime": "2026-04-07T16:00:00Z", "timeZone": "UTC"},
        "recurrence": ["RRULE:FREQ=WEEKLY;COUNT=3"],
    }
    moved = {
        "id": "aa-review-moved",
        "status": "confirmed",
        "recurringEventId": "zz-review",
        "originalStartTime": {"dateTime": "2026-04-14T15:00:00Z", "timeZone": "UTC"},
        "summary": "Review (moved)",
        "start": {"dateTime": "2026-04-14T17:00:00Z", "timeZone": "UTC"},
        "end": {"dateTime": "2026-04-14T18:00:00Z", "timeZone": "UTC"},
    }
    for event in (master, moved):
        connected.google.put("work-token", "work@example.test", event)
    with system_context(reason="tests.google"):
        CalendarSource.objects.filter(pk=connected.sources["work-token"].pk).update(config={"sync_page_bound": 1})
    for _poll in range(2):
        connected.poll()

    events = connected.events()
    override = events["zz-review#20260414T150000Z"]
    assert (override.series_id, override.title) == (events["zz-review"].pk, "Review (moved)")
    with system_context(reason="tests.google"):
        failures = RecordLink.objects.filter(stream__integration_id=connected.sources["work-token"].pk)
        assert not [row.code for link in failures for row in link.failures(open_only=True)]


def test_an_all_day_exception_reads_its_original_day_in_its_series_zone(connected: Connected) -> None:
    offsite = {
        "id": "offsite",
        "status": "confirmed",
        "summary": "Offsite",
        "start": {"date": "2026-04-06", "timeZone": "Asia/Tokyo"},
        "end": {"date": "2026-04-07", "timeZone": "Asia/Tokyo"},
        "recurrence": ["RRULE:FREQ=WEEKLY;COUNT=3"],
    }
    moved = {
        "id": "offsite_20260413",
        "status": "confirmed",
        "recurringEventId": "offsite",
        "originalStartTime": {"date": "2026-04-13"},
        "summary": "Offsite (moved)",
        "start": {"date": "2026-04-14"},
        "end": {"date": "2026-04-15"},
    }
    for event in (offsite, moved):
        connected.google.put("work-token", "work@example.test", event)
    connected.poll()

    events = connected.events()
    tokyo = ZoneInfo("Asia/Tokyo")
    # The work calendar is New York's; the series expands at Tokyo midnight, and so is its exception keyed.
    override = events["offsite#20260412T150000Z"]
    assert (override.series_id, override.original_start) == (events["offsite"].pk, _utc(2026, 4, 13, zone=tokyo))


def test_a_cancelled_master_with_linked_exceptions_withdraws_the_series(connected: Connected) -> None:
    connected.poll()
    connected.google.put("work-token", "work@example.test", {"id": "standup", "status": "cancelled"})
    connected.poll()

    events = connected.events()
    assert events["standup"].status == "cancelled"
    with system_context(reason="tests.google"):
        shown = CalendarEvent.objects.filter(Q(pk=events["standup"].pk) | Q(series=events["standup"]))
        titles = {o.title for o in shown.occurrences(_utc(2026, 3, 1), _utc(2026, 4, 1))}
        failures = RecordLink.objects.filter(stream__integration_id=connected.sources["work-token"].pk)
        assert not [row.code for link in failures for row in link.failures(open_only=True)]
    assert "Standup" not in titles


@pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required.")
def test_two_racing_connects_of_one_account_make_one_source(connected: Connected) -> None:
    third = _account(connected.client, connected.user, "third", "third-token")
    connected.google.accounts["third-token"] = Account()

    def contend() -> Any:
        return _connect(connected.user, third)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            # The first connect holds the user's and the credential's rows until this block commits.
            _connect(connected.user, third)
            contender, pid = submit(pool, contend)
            wait_for_lock(pid, contender)
        with pytest.raises(ValidationError, match="already connected"):
            contender.result(timeout=10)

    with system_context(reason="tests.google"):
        assert CalendarSource.objects.filter(credential=third).count() == 1

