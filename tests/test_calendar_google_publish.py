"""Publishing a local calendar to a Google resource calendar, through the real verbs, stream driver and client.

A fake Google Calendar API answers through integrate's ``mock_transport``: the
calendars a service account writes keep their events with ETags, honour
``If-Match`` (``412``), refuse a taken id (``409``), find events by their private
stamp, and expand a series' instances. The key mints its token at a fake token
endpoint, which checks the assertion it signs. Nothing patches the backend.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit
from zoneinfo import ZoneInfo

import httpx2
import pytest
from django.core.cache import caches
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from joserfc import jwt
from rebac import actor_context, system_context

from angee.calendar import schema as calendar_schema
from angee.calendar.tasks import purge_unsubscribed_sources
from angee.calendar.testing.models import Calendar, CalendarEvent, CalendarSource
from angee.calendar_integrate_google.backend import CALENDAR_EVENTS, STAMP, event_id
from angee.calendar_integrate_google.connect import connect_google_resource_calendars
from angee.graphql.testing.documents import execute_schema, result_data
from angee.integrate.models import CredentialStatus
from angee.integrate.states import ConflictKeep, LinkStatus, StreamDirection, SyncFailureKind
from angee.integrate.streams import BridgeSyncError, sync_bridge
from angee.integrate.testing.http import mock_transport, request_fields
from angee.integrate.testing.models import RecordLink, SyncStream
from angee.jobs.celery import app as celery_app
from angee.messaging.testing.models import Person
from tests.conftest import Credential, ExternalAccount, OAuthClient, Vendor, addon_schema, create_user
from tests.lock_waits import submit, wait_for_lock
from tests.test_integrate_jwt_bearer import TOKEN_ENDPOINT, create_jwt_bearer, google_key_file, signing_key

pytestmark = pytest.mark.django_db(transaction=True)

BERLIN = ZoneInfo("Europe/Berlin")
API = "/calendar/v3/"
ROOM = "c_room-1@resource.calendar.google.com"
OTHER_ROOM = "c_room-2@resource.calendar.google.com"


def _berlin(month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """Build a Berlin wall-clock moment in 2026 as aware UTC."""

    return datetime(2026, month, day, hour, minute, tzinfo=BERLIN).astimezone(UTC)


def _basic(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


@dataclass
class FakeCalendar:
    """Google Calendar v3 for the calendars one service account writes."""

    calendars: dict[str, dict[str, dict[str, Any]]] = field(default_factory=lambda: {ROOM: {}, OTHER_ROOM: {}})
    version: int = 0
    lagging: bool = False
    """Stamp lookups miss, as Google's extended-property index may right after a create."""
    lost_inserts: int = 0
    """Inserts that land but whose answer never arrives: a crash between Google's create and the link."""
    scopes: list[str] = field(default_factory=list)
    requests: list[httpx2.Request] = field(default_factory=list)

    def edit(self, calendar_id: str, event: str, **fields: Any) -> None:
        """Change one stored event as a person editing it in Google does: a new ETag."""

        self._store(self.calendars[calendar_id], {**self.calendars[calendar_id][event], **fields})

    def writes(self) -> list[httpx2.Request]:
        return [request for request in self.requests if request.method in {"POST", "PATCH", "PUT", "DELETE"}
                and str(request.url) != TOKEN_ENDPOINT]

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if str(request.url) == TOKEN_ENDPOINT:
            claims = jwt.decode(request_fields(request)["assertion"], signing_key(), algorithms=["RS256"]).claims
            self.scopes.append(claims["scope"])
            return httpx2.Response(200, json={"access_token": "room-token", "token_type": "Bearer", "expires_in": 3600})
        assert request.headers["authorization"] == "Bearer room-token"
        url = urlsplit(str(request.url))
        params = {key: values[0] for key, values in parse_qs(url.query).items()}
        parts = [unquote(part) for part in url.path.removeprefix(API).split("/")]
        events = self.calendars[parts[1]]
        body = json.loads(request.content) if request.content else {}
        if parts[2:] == ["events"] and request.method == "GET":
            name, _equals, value = params["privateExtendedProperty"].partition("=")
            found = [] if self.lagging else [row for row in events.values() if _stamp(row, name) == value]
            return httpx2.Response(200, json={"items": found})
        if parts[2:] == ["events"]:
            assert params["sendUpdates"] == "none"
            if body["id"] in events:
                return httpx2.Response(409, json={"error": {"code": 409, "errors": [{"reason": "duplicate"}]}})
            stored = self._store(events, body)
            if self.lost_inserts:
                self.lost_inserts -= 1
                raise httpx2.ReadTimeout("The answer was lost.", request=request)
            return httpx2.Response(200, json=stored)
        if len(parts) == 5:
            original = datetime.fromisoformat(params["originalStart"].replace("Z", "+00:00"))
            found_instance = self._instance(events, f"{parts[3]}_{_basic(original)}")
            return httpx2.Response(200, json={"items": [found_instance] if found_instance else []})
        event = events.get(parts[3]) or self._instance(events, parts[3])
        if event is None:
            return httpx2.Response(404, json={"error": {"code": 404, "errors": [{"reason": "notFound"}]}})
        if request.method == "GET":
            return httpx2.Response(200, json=event)
        assert request.method == "PATCH" and params["sendUpdates"] == "none"
        if request.headers.get("if-match") != event["etag"]:
            return httpx2.Response(412, json={"error": {"code": 412, "errors": [{"reason": "conditionNotMet"}]}})
        return httpx2.Response(200, json=self._store(events, {**event, **body}))

    def _store(self, events: dict[str, dict[str, Any]], event: dict[str, Any]) -> dict[str, Any]:
        self.version += 1
        events[event["id"]] = {**event, "etag": f'"{self.version}"'}
        return events[event["id"]]

    def _instance(self, events: dict[str, dict[str, Any]], instance: str) -> dict[str, Any] | None:
        """Return a stored exception, else the unchanged instance its series expands to."""

        if instance in events:
            return events[instance]
        series, _sep, basic = instance.rpartition("_")
        master = events.get(series)
        if master is None or not master.get("recurrence"):
            return None
        original = datetime.strptime(basic, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        start = datetime.fromisoformat(master["start"]["dateTime"])
        span = datetime.fromisoformat(master["end"]["dateTime"]) - start
        zone = master["start"]["timeZone"]
        unchanged = {name: master[name] for name in ("summary", "description", "location", "status", "transparency",
                                                       "visibility", "extendedProperties") if name in master}
        return {
            **unchanged,
            "id": instance,
            "etag": master["etag"],
            "recurringEventId": series,
            "originalStartTime": {"dateTime": original.isoformat(), "timeZone": zone},
            "start": {"dateTime": original.isoformat(), "timeZone": zone},
            "end": {"dateTime": (original + span).isoformat(), "timeZone": zone},
        }


def _stamp(row: dict[str, Any], name: str = STAMP) -> str:
    return str(((row.get("extendedProperties") or {}).get("private") or {}).get(name) or "")


@dataclass
class Published:
    """A user's service-account key connected as a publishing source, a local calendar, and the fake Google."""

    user: Any
    google: FakeCalendar
    key: Any
    source: Any
    calendar: Any

    def poll(self) -> int:
        with system_context(reason="tests.google.publish.poll"):
            return sync_bridge(CalendarSource.objects.get(pk=self.source.pk))

    def publish(self, remote_id: str = ROOM) -> Any:
        return CalendarSource.objects.sudo(reason="tests.google.publish").get(pk=self.source.pk).publish(
            self.calendar, remote_id
        )

    def event(self, **fields: Any) -> Any:
        """Create one validated event on the calendar, as its write path does."""

        with system_context(reason="tests.google.publish.event"):
            row = CalendarEvent(calendar=self.calendar, **{"title": "Workshop", **fields})
            row.full_clean()
            row.save()
            return row

    def links(self, key: str = "events") -> dict[str, Any]:
        rows = RecordLink.objects.sudo(reason="tests.google.publish").filter(
            stream__integration_id=self.source.pk, stream__key=key
        )
        return {link.external_key: link for link in rows}

    def remote(self, calendar_id: str = ROOM) -> dict[str, dict[str, Any]]:
        return self.google.calendars[calendar_id]


@pytest.fixture
def published() -> Iterator[Published]:
    caches["default"].clear()
    google = FakeCalendar()
    with mock_transport(google):
        user = create_user("room-publisher")
        with system_context(reason="tests.google.publish.setup"):
            Vendor._base_manager.get_or_create(slug="google", defaults={"display_name": "Google"})
            person = Person.objects.create(display_name="Room 1")
            calendar = Calendar.objects.create(party=person, name="Room 1", timezone="Europe/Berlin", owner=user)
        key = create_jwt_bearer("room-writer", google_key_file(), user=user)
        with actor_context(user):
            source = connect_google_resource_calendars(user, key)
        yield Published(user, google, key, source, calendar)


def test_a_local_event_pushes_exactly_one_remote_event(published: Published) -> None:
    published.publish()
    workshop = published.event(start=_berlin(5, 4, 10), end=_berlin(5, 4, 12), location="Room 1")
    published.poll()

    key = event_id(workshop.sqid)
    remote = published.remote()
    assert list(remote) == [key]
    assert (remote[key]["summary"], remote[key]["location"]) == ("Workshop", "Room 1")
    assert _stamp(remote[key]) == workshop.sqid
    assert remote[key]["start"] == {"dateTime": _berlin(5, 4, 10).isoformat(), "timeZone": "Europe/Berlin"}
    assert "attendees" not in remote[key]
    # The key mints for the events scope alone: no delegation, no other grant.
    assert published.google.scopes == [CALENDAR_EVENTS]
    link = published.links()[key]
    assert (link.status, link.target_object_id, link.remote_version) == (
        LinkStatus.CURRENT, str(workshop.pk), remote[key]["etag"]
    )

    # Nothing changed: the next push sends nothing at all.
    seen = len(published.google.requests)
    published.poll()
    assert published.google.requests[seen:] == []


@pytest.mark.parametrize("lagging", [False, True], ids=["found-by-stamp", "taken-id"])
def test_a_crash_between_create_and_link_still_makes_one_remote_event(published: Published, lagging: bool) -> None:
    published.publish()
    workshop = published.event(start=_berlin(5, 4, 10), end=_berlin(5, 4, 12))
    published.google.lost_inserts = 1
    published.google.lagging = lagging

    with pytest.raises(BridgeSyncError):
        published.poll()
    key = event_id(workshop.sqid)
    assert list(published.remote()) == [key]
    assert published.links()[key].target_object_id is None

    published.poll()

    # The retry found the first create (its stamp, or its id Google says is taken) and adopted it.
    assert list(published.remote()) == [key]
    inserts = [request for request in published.google.writes() if request.method == "POST"]
    assert len(inserts) == (2 if lagging else 1)
    link = published.links()[key]
    assert (link.status, link.target_object_id, link.remote_version) == (
        LinkStatus.CURRENT, str(workshop.pk), published.remote()[key]["etag"]
    )


def test_a_remote_edit_holds_the_next_local_edit_as_a_conflict(published: Published) -> None:
    published.publish()
    workshop = published.event(start=_berlin(5, 4, 10), end=_berlin(5, 4, 12))
    published.poll()
    key = event_id(workshop.sqid)
    published.google.edit(ROOM, key, summary="Taken by facilities")

    with system_context(reason="tests.google.publish.edit"):
        workshop.title = "Workshop (moved)"
        workshop.save(update_fields=["title", "updated_at"])
    published.poll()

    # Google answered 412: the remote edit stands, and the local edit waits as a held conflict.
    assert published.remote()[key]["summary"] == "Taken by facilities"
    [patch] = [request for request in published.google.writes() if request.method == "PATCH"]
    assert patch.headers["if-match"] != published.remote()[key]["etag"]
    link = published.links()[key]
    assert [(row.kind, row.code) for row in link.failures(open_only=True)] == [
        (SyncFailureKind.CONFLICT, "remote_version_changed")
    ]

    # A held conflict is never written again until a person resolves it.
    seen = len(published.google.writes())
    published.poll()
    assert published.google.writes()[seen:] == []
    assert published.remote()[key]["summary"] == "Taken by facilities"

    # Keeping Angee's side reads the remote version, then writes over exactly that version.
    with system_context(reason="tests.google.publish.resolve"):
        [conflict] = RecordLink.objects.get(pk=link.pk).failures(open_only=True)
        conflict.resolve_conflict(keep=ConflictKeep.LOCAL)
    assert published.remote()[key]["summary"] == "Workshop (moved)"
    link = published.links()[key]
    assert (link.failures(open_only=True), link.remote_version) == ((), published.remote()[key]["etag"])


def test_a_series_maps_to_its_rule_and_its_moved_and_cancelled_instances(published: Published) -> None:
    published.publish()
    standup = published.event(
        title="Standup", start=_berlin(5, 4, 9), end=_berlin(5, 4, 9, 30), recurrence="FREQ=WEEKLY;COUNT=4"
    )
    published.event(
        title="Standup (moved)", series=standup, original_start=_berlin(5, 11, 9),
        start=_berlin(5, 11, 10), end=_berlin(5, 11, 10, 45),
    )
    published.event(
        title="Standup", series=standup, original_start=_berlin(5, 18, 9),
        start=_berlin(5, 18, 9), end=_berlin(5, 18, 9, 30), status="cancelled",
    )
    published.poll()

    master = event_id(standup.sqid)
    remote = published.remote()
    assert remote[master]["recurrence"] == ["RRULE:FREQ=WEEKLY;COUNT=4"]
    assert remote[master]["start"]["timeZone"] == "Europe/Berlin"
    moved, cancelled = remote[f"{master}_{_basic(_berlin(5, 11, 9))}"], remote[f"{master}_{_basic(_berlin(5, 18, 9))}"]
    assert (moved["recurringEventId"], moved["summary"], moved["start"]["dateTime"]) == (
        master, "Standup (moved)", _berlin(5, 11, 10).isoformat()
    )
    assert (cancelled["recurringEventId"], cancelled["status"]) == (master, "cancelled")
    # An instance is patched with neither a rule nor a stamp of its own.
    sent = [json.loads(request.content) for request in published.google.writes() if request.method == "PATCH"]
    assert len(sent) == 2 and not any({"recurrence", "extendedProperties"} & set(body) for body in sent)
    links = published.links()
    assert set(links) == {master, f"{master}#{_basic(_berlin(5, 11, 9))}", f"{master}#{_basic(_berlin(5, 18, 9))}"}
    assert all(link.status == LinkStatus.CURRENT for link in links.values())


def test_a_calendar_the_deployment_does_not_publish_to_is_refused_before_any_call(
    published: Published, settings: Any
) -> None:
    settings.ANGEE_CALENDAR_PUBLISH_TARGETS = [OTHER_ROOM]
    with pytest.raises(ValidationError, match="does not publish to that calendar"):
        published.publish(ROOM)

    settings.ANGEE_CALENDAR_PUBLISH_TARGETS = None
    published.publish(ROOM)
    workshop = published.event(start=_berlin(5, 4, 10), end=_berlin(5, 4, 12))
    # A test profile publishes nowhere: the push refuses before minting a token or calling Google.
    settings.ANGEE_CALENDAR_PUBLISH_TARGETS = []
    published.poll()

    assert published.google.requests == []
    link = published.links()[event_id(workshop.sqid)]
    assert [(row.kind, row.code) for row in link.failures(open_only=True)] == [
        (SyncFailureKind.REMOTE_REJECTED, "target_not_allowed")
    ]


def test_publish_binds_a_local_calendar_once_and_refuses_another_binding(published: Published) -> None:
    schema = addon_schema(calendar_schema.schemas, "console")
    mutation = (
        f'mutation {{ publish_calendar(id: "{published.source.public_id}", '
        f'calendar_id: "{published.calendar.public_id}", remote_id: "{ROOM}") {{ ok }} }}'
    )
    assert result_data(execute_schema(schema, mutation, user=published.user))["publish_calendar"] == {"ok": True}

    with system_context(reason="tests.google.publish"):
        calendar = Calendar.objects.get(pk=published.calendar.pk)
        assert calendar.source_id == published.source.pk and calendar.is_writable
        [binding] = published.links("calendars").values()
        assert (binding.external_key, binding.target_object_id) == (ROOM, str(calendar.pk))
        streams = SyncStream.objects.filter(integration_id=published.source.pk)
        assert {(row.key, row.direction, row.reconcile_interval) for row in streams} >= {
            ("calendars", StreamDirection.PUSH, None)
        }
    published.poll()
    with system_context(reason="tests.google.publish"):
        events_stream = SyncStream.objects.get(integration_id=published.source.pk, key="events")
        assert (events_stream.partition, events_stream.container_id, events_stream.direction) == (
            ROOM, binding.pk, StreamDirection.PUSH
        )

    # Publishing again changes nothing; any other binding is refused.
    assert published.publish(ROOM).pk == binding.pk
    with pytest.raises(ValidationError, match="already published to another calendar"):
        published.publish(OTHER_ROOM)
    with system_context(reason="tests.google.publish"):
        person = Person.objects.create(display_name="Room 2")
        other_calendar = Calendar.objects.create(party=person, name="Room 2", owner=published.user)
    with pytest.raises(ValidationError, match="already publishes another calendar there"):
        CalendarSource.objects.sudo(reason="tests").get(pk=published.source.pk).publish(other_calendar, ROOM)
    second_key = create_jwt_bearer("room-writer-2", google_key_file(), user=published.user)
    with actor_context(published.user):
        second = connect_google_resource_calendars(published.user, second_key)
    with pytest.raises(ValidationError, match="Another calendar source"):
        second.publish(published.calendar, OTHER_ROOM)


def test_a_publishing_source_admits_only_a_service_account_key(published: Published) -> None:
    with system_context(reason="tests.google.publish"):
        client = OAuthClient.objects.create(
            slug="google", display_name="Google", client_id="google-cid",
            authorize_endpoint="https://accounts.example.test/auth", token_endpoint="https://accounts.example.test/token",
        )
    account = ExternalAccount.objects.connect_oauth(
        client,
        user=published.user,
        external_id="person",
        tokens={"access_token": "person-token", "token_type": "Bearer", "scope": f"openid email {CALENDAR_EVENTS}"},
        claims={"sub": "person", "email": "person@example.test"},
    )
    with actor_context(published.user), pytest.raises(ValidationError, match="service-account key"):
        connect_google_resource_calendars(published.user, account.credential)
    with system_context(reason="tests.google.publish"), pytest.raises(ValidationError, match="service-account key"):
        CalendarSource.objects.get(pk=published.source.pk).attach_credential(account.credential)
    with system_context(reason="tests.google.publish"):
        assert CalendarSource.objects.get(pk=published.source.pk).credential_id == published.key.pk


def test_unpublishing_unbinds_the_calendar_and_keeps_its_events_and_key(
    published: Published, monkeypatch: pytest.MonkeyPatch
) -> None:
    published.publish()
    workshop = published.event(start=_berlin(5, 4, 10), end=_berlin(5, 4, 12))
    published.poll()
    monkeypatch.setattr(celery_app, "send_task", lambda name, **options: None)
    with system_context(reason="tests.google.publish"):
        CalendarSource.objects.get(pk=published.source.pk).unsubscribe()
    seen = len(published.google.requests)

    purge_unsubscribed_sources()

    assert published.google.requests[seen:] == []
    with system_context(reason="tests.google.publish"):
        calendar = Calendar.objects.get(pk=published.calendar.pk)
        assert (calendar.source_id, calendar.is_archived) == (None, False)
        assert CalendarEvent.objects.filter(pk=workshop.pk).exists()
        assert all(link.target_object_id is None for link in [*published.links().values(),
                                                              *published.links("calendars").values()])
        assert CalendarSource.objects.get(pk=published.source.pk).credential_id is None
        assert Credential.objects.get(pk=published.key.pk).status == CredentialStatus.ACTIVE
        # The calendar is local again: its events are its own to delete.
        CalendarEvent.objects.get(pk=workshop.pk).delete()
    assert list(published.remote()) == [event_id(workshop.sqid)]


@pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required.")
def test_two_racing_publishes_of_one_calendar_bind_it_once(published: Published) -> None:
    second_key = create_jwt_bearer("room-writer-2", google_key_file(), user=published.user)
    with actor_context(published.user):
        second = connect_google_resource_calendars(published.user, second_key)

    def contend() -> Any:
        return second.publish(published.calendar, OTHER_ROOM)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            # The first publish holds the calendar's row until this block commits.
            published.publish(ROOM)
            contender, pid = submit(pool, contend)
            wait_for_lock(pid, contender)
        with pytest.raises(ValidationError, match="Another calendar source"):
            contender.result(timeout=10)

    with system_context(reason="tests.google.publish"):
        assert Calendar.objects.get(pk=published.calendar.pk).source_id == published.source.pk
        bound = RecordLink.objects.filter(stream__key="calendars", target_object_id=str(published.calendar.pk))
        assert [link.stream.integration_id for link in bound] == [published.source.pk]
