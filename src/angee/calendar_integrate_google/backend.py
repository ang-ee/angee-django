"""Google Calendar: sync each calendar of a connected account in, or publish local calendars out.

**Sync in** (:class:`GoogleCalendarBackend`).

The account's calendar list is the source's ``calendars`` stream: one record per
calendar, keyed by its Google calendar id, stating its name (the account's own
``summaryOverride``, else its ``summary``), colour and zone. Each calendar's
events stream pages ``events.list`` with ``showDeleted=true``: without a sync
token in its cursor it lists the calendar in full (a baseline), with one only what
changed since; the last page's ``nextSyncToken`` commits with that page. A
``410 Gone`` clears the token: the driver starts a new baseline
(``CursorInvalid``). The calendar's base backend owns the event projection and
every write; integrate's absence sweep lists the calendar in full once per poll
interval for the events Google stopped serving.

Keys follow the calendar base (``series_key``, ``instance_key``): a series master
or single event is keyed by its Google event id, and an exception of a series by
its master's id plus the instance's original start, so each remote instance owns
its own placement. An exception carrying ``recurringEventId`` and
``originalStartTime`` is an override row; a cancelled exception is a cancelled
override, and so is each ``EXDATE`` of a master's ``recurrence``. A cancelled
master or single event is a removal (a tombstone). The ``recurrence`` lines are
RFC 5545 and read through icalendar: only the first ``RRULE`` is kept
(``series_rule``), and ``RDATE`` and ``EXRULE`` are not mapped.

Mapped fields: ``summary``, ``description``, ``location``; ``start``/``end``
(``date`` is all-day, local midnight in the event's or the calendar's zone;
``dateTime`` is its instant, and ``start.timeZone`` the zone the event keeps, so
its series follows that wall clock across DST); ``status``; ``transparency``
(``transparent`` is free); ``visibility`` (``public``, ``private`` and
``confidential``); the organizer and attendees with their RSVP (``optional`` makes
an optional attendee); the conference URL (a video entry point, else
``hangoutLink``).

The calendars are synced, not locally writable: the backend pulls (its ``direction``
is ``PULL``) and reads with ``calendar.readonly``.

**Publish out** (:class:`GooglePublishingCalendarBackend`). A publishing source writes
local calendars to any Google calendar shared with its service-account key, a
Workspace resource calendar among them, and never reads them back. A new master or single event
is created under a Google id derived from its public id (:func:`event_id`), stamped
with that public id (``extendedProperties.private.angee_event``), and found by its
stamp before any insert. A series' rule is its ``recurrence`` line; each override
patches its instance (a cancelled override cancels it). Every later write names the
ETag Angee last wrote, so a remote edit surfaces as Google's ``412`` the next time
Angee writes that event: a held conflict, never an overwrite. An event deleted
locally is deleted in Google, and an override deleted locally restores its instance,
on the same condition. Google's refusal of one event sets that event aside; the
calendar's other events still push.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from time import monotonic
from typing import Any, ClassVar

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from icalendar import Component, Event
from rebac import system_context

from angee.base.serialization import canonical_json_sha256
from angee.calendar.backends import (
    EVENTS_STREAM,
    CalendarBackend,
    ParsedAttendee,
    ParsedCalendar,
    ParsedEvent,
    PublishingCalendarBackend,
    cancelled_instances,
    canonical_rule,
    event_columns,
    excluded_dates,
    instance_key,
    organizer_first,
    parse_key,
    series_key,
    series_rule,
)
from angee.calendar.choices import AttendeeResponse, AttendeeRole, EventStatus, EventTransparency, EventVisibility
from angee.calendar_integrate_google.client import (
    EventIdTaken,
    EventRefused,
    GoogleCalendarClient,
    PageTokenInvalid,
    PreconditionFailed,
    SyncTokenExpired,
)
from angee.integrate.credentials import CredentialKind
from angee.integrate.errors import IntegrationError
from angee.integrate.models import IntegrationLifecycle
from angee.integrate.states import UNSET
from angee.integrate.streams import (
    CursorInvalid,
    LocalChange,
    RecordChange,
    RemoteRejected,
    StreamPage,
    WriteBackResult,
)
from angee.integrate.sync import sync_deadline
from angee.scheduling.zones import get_zone_info, get_zone_names

CALENDAR_READONLY = "https://www.googleapis.com/auth/calendar.readonly"
"""The OAuth scope sync-in reads every calendar of the account with."""

CALENDAR_EVENTS = "https://www.googleapis.com/auth/calendar.events"
"""The scope a publishing source's key mints for: it writes the events of the calendars shared with it."""

STAMP = "angee_event"
"""The private extended property every published master or single event carries: its Angee public id.

It names no deployment, since no Angee instance identity exists to name: two
deployments publishing to one remote calendar could adopt each other's events.
"""

_SWEEP_PAGE = 250
"""Events per page of a sweep's full listing: well under the transport's 8 MiB answer cap."""

_STATUSES = {
    "confirmed": EventStatus.CONFIRMED,
    "tentative": EventStatus.TENTATIVE,
    "cancelled": EventStatus.CANCELLED,
}
"""Google's event ``status`` in the calendar's vocabulary; an unknown one reads as confirmed."""

_TRANSPARENCIES = {"opaque": EventTransparency.BUSY, "transparent": EventTransparency.FREE}
"""Google's ``transparency``; an unknown one reads as busy."""

_VISIBILITIES = {
    "default": EventVisibility.DEFAULT,
    "public": EventVisibility.PUBLIC,
    "private": EventVisibility.PRIVATE,
    "confidential": EventVisibility.PRIVATE,
}
"""Google's ``visibility``; an unknown one reads as default."""


def _written(table: Mapping[str, str]) -> dict[str, str]:
    """Return a Google-to-Angee table the other way: the first Google value naming an Angee value is what is written."""

    inverse: dict[str, str] = {}
    for google, angee in table.items():
        inverse.setdefault(angee, google)
    return inverse


_WRITTEN_STATUSES = _written(_STATUSES)
_WRITTEN_TRANSPARENCIES = _written(_TRANSPARENCIES)
_WRITTEN_VISIBILITIES = _written(_VISIBILITIES)

_UNREADABLE = (ArithmeticError, AttributeError, KeyError, TypeError, ValueError, ValidationError)
"""What mapping a Google event Angee cannot read raises: a missing or malformed field, a zone or rule it refuses."""

_PUBLISHED_COLUMNS = (
    "title", "description", "location", "start", "end", "all_day", "status", "transparency", "visibility"
)
"""The event columns a published event writes as they stand (``event_columns``); attendees and links stay local."""


def _sweep(stream: Any) -> tuple[Any, ...]:
    """Return the identity of the absence sweep running on ``stream``: its calendar, generation and start."""

    started = (stream.reconcile_state or {}).get("started_at")
    return (stream.partition, stream.generation, started) if started else ()


_RESPONSES = {
    "accepted": AttendeeResponse.ACCEPTED,
    "declined": AttendeeResponse.DECLINED,
    "tentative": AttendeeResponse.TENTATIVE,
}


class _GoogleBackend(CalendarBackend):
    """What both Google backends share: one Calendar client per adapter, over the source's credential."""

    _calendar_client: GoogleCalendarClient | None = None

    def _client_scopes(self) -> tuple[str, ...]:
        """Return the scopes this backend's requests ask the source's credential for."""

        raise NotImplementedError("Google calendar backends name the scopes their client asks for.")

    def _client(self) -> GoogleCalendarClient:
        """Return this adapter's client, built once over the source's credential as loaded for this sync.

        One client per adapter: its transport renews a rejected token once per sync,
        not once per page.
        """

        if self._calendar_client is None:
            credential = self.bridge.fresh_credential()
            if credential is None:
                raise IntegrationError("This Google calendar source has no connected account.")
            self._calendar_client = GoogleCalendarClient(credential, self._client_scopes())
        return self._calendar_client


class GoogleCalendarBackend(_GoogleBackend):
    """Syncs every calendar of one connected Google account into read-only calendars.

    One source per account: a user with several Google accounts connects each as
    its own source. The account's OAuth credential comes from the deployment's
    ``google`` client (``integrate_google``) with :data:`CALENDAR_READONLY` granted;
    no other credential is admitted (:meth:`admit_credential`).
    """

    key = "google"
    label = "Google Calendar"
    icon = "google"
    defaults: ClassVar[dict[str, Any]] = {"vendor": "google", "poll_interval": 900}
    oauth_client: ClassVar[str] = "google"
    oauth_scopes: ClassVar[tuple[str, ...]] = (CALENDAR_READONLY,)

    _calendars: list[ParsedCalendar] | None = None
    _held: _GooglePage | None = None
    _listing: tuple[tuple[Any, ...], dict[str, dict[str, Any] | None]] | None = None
    """The current sweep's full listing of one calendar: its sweep identity, then its payloads by key."""

    def admit_credential(self, credential: Any) -> None:
        """Admit only an account the source reads with that no other source syncs.

        The source reads with an OAuth account of its ``google`` client that
        granted :attr:`oauth_scopes` (``Integration.reads_with``). Integrate asks on
        every attachment (a connect, the console's credential patch, a reconnect),
        so one account is one source on every path. The one-source rule runs under
        the credential's row lock, held to the caller's commit, so two racing
        attachments of one account serialize and the second is refused.
        """

        if not self.bridge.reads_with(credential):
            raise ValidationError(
                {"credential": "This account cannot read Google calendars; connect it with Calendar access."}
            )
        with system_context(reason="calendar_integrate_google.admit_credential"), transaction.atomic():
            apps.get_model("integrate", "Credential").objects.lock_if_supported().get(pk=credential.pk)
            same = Q(credential_id=credential.pk)
            if credential.external_account_id is not None:
                same |= Q(account_id=credential.external_account_id)
            synced = (
                type(self.bridge).system_queryset()
                .filter(same, backend_class=self.key)
                .exclude(lifecycle=IntegrationLifecycle.DISCONNECTED)
            )
            if synced.exclude(pk=self.bridge.pk).exists():
                raise ValidationError({"credential": "This Google account's calendars are already connected."})

    # --- calendars ---

    def discover(self) -> list[ParsedCalendar]:
        """Return the account's calendars from its calendar list, read once per adapter."""

        if self._calendars is None:
            calendars = []
            for entry in self._client().calendar_list():
                self._require_time()
                if entry.get("id"):
                    calendars.append(
                        ParsedCalendar(
                            key=str(entry["id"]),
                            name=str(entry.get("summaryOverride") or entry.get("summary") or ""),
                            timezone=_known_zone(entry.get("timeZone")),
                            color=str(entry.get("backgroundColor") or ""),
                        )
                    )
            self._calendars = calendars
        return self._calendars

    # --- events ---

    def extract_events(self, stream: Any, page_bound: int, *, deadline: float | None = None) -> StreamPage:
        """Page one ``events.list`` page in key order; the token that follows it commits with its last record.

        A Google page may map to more records than ``page_bound`` (a master's
        ``EXDATE``s): the base pager's cursor then names the Google request
        (``from``) and the last key paged. An adapter that no longer holds that
        page asks Google for it again and pages it from its first key, since what
        Google answers now may hold keys before the last one paged; the driver
        leaves an unchanged record unapplied. A removed event that was never linked
        is left out.
        """

        cursor = dict(stream.cursor or {})
        request = dict(cursor["from"]) if "from" in cursor else cursor
        after = cursor.get("after")
        held = self._held
        if held is None or (held.partition, held.generation, held.request) != (
            stream.partition, stream.generation, request
        ):
            # One adapter drains several calendars, and a reset starts a generation: a held page is one of each.
            held = self._held = self._fetch(stream, request, page_bound)
            after = None
        return self.page(
            stream,
            held.payloads,
            page_bound,
            after=after,
            cursor=held.following,
            resume=request,
            last="page_token" not in held.following,
        )

    def read_events(self, stream: Any, keys: Sequence[str]) -> dict[str, dict[str, Any] | None]:
        """Re-read each requested key; a key Google no longer serves is ``None``.

        The absence sweep reads what its listing just served (:meth:`event_keys`), so
        it asks Google nothing more. Otherwise a master or single event is read by its
        id, and an instance through its series' ``instances`` at its original start,
        or as its master's ``EXDATE``.
        """

        listing, sweep = self._listing, _sweep(stream)
        if listing is not None and sweep and listing[0] == sweep:
            return {key: listing[1].get(key) for key in keys}
        client = self._client()
        zone = self.get_calendar(stream).timezone
        found: dict[str, dict[str, Any] | None] = {}
        masters: dict[str, dict[str, Any] | None] = {}
        for key in keys:
            master_id, moment = parse_key(key)
            if master_id is None:
                # A digest names no Google id: quarantine the record rather than read it as removed.
                found[key] = {"error": "unreadable_identity"}
                continue
            if master_id not in masters:
                masters[master_id] = client.event(stream.partition, master_id)
            master = masters[master_id]
            if moment is None or master is None:
                found[key] = dict(self._observe_item(master, zone)).get(key) if master is not None else None
                continue
            day_zone = (_series_zone(master) or zone) if "date" in (master.get("start") or {}) else None
            item = client.instance(stream.partition, master_id, moment, all_day_zone=day_zone)
            observed = dict(self._observe_item(master, zone))
            if item is not None:
                observed.update(self._observe_item(item, zone, series_zones={master_id: _series_zone(master)}))
            found[key] = observed.get(key)
        return found

    def event_keys(self, stream: Any) -> list[str]:
        """Return every key the calendar serves now, removals left out, from one full listing per sweep.

        A sweep enumerates in bounded pulses; the adapter lists the calendar once for
        the sweep (keyed by its partition, generation and start) and serves the
        sweep's later pulses and its identity reads (:meth:`read_events`) from it.
        """

        sweep = _sweep(stream)
        if self._listing is None or self._listing[0] != sweep:
            zone = self.get_calendar(stream).timezone
            client = self._client()
            payloads: dict[str, dict[str, Any] | None] = {}
            token = ""
            while True:
                self._require_time()
                page = client.events_page(stream.partition, page_token=token, max_results=_SWEEP_PAGE)
                items = list(page.get("items") or ())
                series_zones = self._series_zones(stream, items)
                for item in items:
                    payloads.update(self._observe_item(item, zone, series_zones=series_zones))
                token = str(page.get("nextPageToken") or "")
                if not token:
                    break
            self._listing = (sweep, payloads)
        return sorted(key for key, payload in self._listing[1].items() if payload is not None)

    def _require_time(self) -> None:
        """Stop a listing that reaches the cycle's deadline (``sync_deadline()``): the next sync lists again."""

        deadline = sync_deadline()
        if deadline is not None and monotonic() > deadline:
            raise IntegrationError(
                "Listing Google Calendar took too long; the next sync lists it again.", transient=True
            )

    # --- mapping ---

    def _fetch(self, stream: Any, request: Mapping[str, Any], page_bound: int) -> _GooglePage:
        """Ask Google for the page ``request`` names and map it; a sync token Google dropped starts a baseline."""

        sync_token = str(request.get("sync_token") or "")
        try:
            page = self._client().events_page(
                stream.partition,
                sync_token=sync_token,
                page_token=str(request.get("page_token") or ""),
                max_results=page_bound,
            )
        except SyncTokenExpired:
            raise CursorInvalid() from None
        except PageTokenInvalid:
            # The listing starts again from the sync token the page continued, else as a new baseline.
            raise CursorInvalid(cursor={"sync_token": sync_token} if sync_token else None) from None
        zone = self.get_calendar(stream).timezone
        items = list(page.get("items") or ())
        series_zones = self._series_zones(stream, items)
        payloads: dict[str, dict[str, Any] | None] = {}
        for item in items:
            payloads.update(self._observe_item(item, zone, series_zones=series_zones))
        removed = [key for key, payload in payloads.items() if payload is None]
        if removed:
            linked = set(stream.links.filter(external_key__in=removed).values_list("external_key", flat=True))
            payloads = {key: payload for key, payload in payloads.items() if payload is not None or key in linked}
        following = (
            {"page_token": str(page["nextPageToken"]), **({"sync_token": sync_token} if sync_token else {})}
            if page.get("nextPageToken")
            else {"sync_token": str(page.get("nextSyncToken") or "")}
        )
        return _GooglePage(stream.partition, stream.generation, dict(request), payloads, following)

    def _series_zones(self, stream: Any, items: Sequence[Mapping[str, Any]]) -> dict[str, str]:
        """Return the zone each series an exception on ``items`` belongs to expands in, by master id.

        A master on the same page states its own; another's is its stored master's
        (an exception may arrive on an earlier page than its master). A series not
        known yet expands in the calendar's zone.
        """

        zones = {str(item["id"]): _series_zone(item) for item in items if item.get("id") and item.get("recurrence")}
        missing = {str(item["recurringEventId"]) for item in items if item.get("recurringEventId")} - set(zones)
        if missing:
            links = stream.links.filter(external_key__in=[series_key(master) for master in missing])
            targets = dict(links.exclude(target_object_id=None).values_list("target_object_id", "external_key"))
            rows = apps.get_model("calendar", "CalendarEvent").system_queryset().filter(pk__in=list(targets))
            for pk, own in rows.values_list("pk", "timezone"):
                zones[targets[str(pk)]] = own
        return {master: own for master, own in zones.items() if own}

    def _observe_item(
        self, item: Mapping[str, Any], zone: str, *, series_zones: Mapping[str, str] | None = None
    ) -> list[tuple[str, dict[str, Any] | None]]:
        """Map one Google event to its keyed payload, then a master's ``EXDATE`` instances.

        ``series_zones`` names the zone a series expands in, by master id
        (:meth:`_series_zones`): an exception's all-day original start is read
        there. A removed master or single event is ``None``; an event the calendar
        cannot take is a quarantine payload under its own key.
        """

        google_id = str(item.get("id") or "")
        if not google_id:
            return []
        series_zone = (series_zones or {}).get(str(item.get("recurringEventId") or "")) or zone
        try:
            key, event = _event(item, zone, series_zone)
            if event is None:
                return [(key, None)]
            observed: list[tuple[str, ParsedEvent]] = [(key, event)]
            if event.recurrence:
                local = event.timezone or zone
                exdates = [_instant(value, tzid, local) for value, tzid in excluded_dates(_recurrence(item))]
                observed.extend(
                    cancelled_instances(
                        event, google_id, exdates, calendar_zone=zone, check_deadline=self._require_time
                    )
                )
        except _UNREADABLE:
            # The driver quarantines semantic input per record; a deadline (``IntegrationError``) stops the listing.
            return [(_own_key(item, series_zone), {"error": "invalid_event"})]
        return [(key, self.observe(event)) for key, event in observed]

    def _client_scopes(self) -> tuple[str, ...]:
        """Read with the scope the account granted at connect: :attr:`oauth_scopes`."""

        return self.oauth_scopes


class GooglePublishingCalendarBackend(_GoogleBackend, PublishingCalendarBackend):
    """Publishes local calendars to any Google calendar shared with a service-account key, resource ones among them.

    A publishing source (``PublishingCalendarBackend``): ``CalendarSource.publish``
    binds a local calendar to a Google calendar id shared with the key's service
    account as writer (a Workspace resource calendar's is
    ``c_…@resource.calendar.google.com``), and that calendar keeps taking local
    edits. The source's credential is the key, a ``jwt_bearer`` credential
    (:meth:`admit_credential`) minted for :data:`CALENDAR_EVENTS` without
    delegation; it connects through no OAuth client. Each event of a published
    calendar is projected (:meth:`local_changes`) and written conditionally
    (:meth:`write_event`); nothing is ever read back to sync.
    """

    key = "google_publishing"
    label = "Google Calendar publishing"
    icon = "google"
    defaults: ClassVar[dict[str, Any]] = {"vendor": "google", "poll_interval": 300}
    oauth_client: ClassVar[str] = ""

    def admit_credential(self, credential: Any) -> None:
        """Admit only a service-account key (``jwt_bearer``) acting as itself: the source never writes as a person.

        A key whose assertions name a delegated user (``Credential.delegated``) is refused.
        """

        if credential.kind != CredentialKind.JWT_BEARER or credential.delegated:
            raise ValidationError(
                {"credential": "Publishing to Google calendars needs a service-account key (JWT bearer) without "
                 "delegation."}
            )

    def _client_scopes(self) -> tuple[str, ...]:
        """Write with :data:`CALENDAR_EVENTS`, the one scope the key mints for."""

        return (CALENDAR_EVENTS,)

    def local_changes(self, stream: Any, *, keys: frozenset[str] | None = None) -> Iterator[LocalChange]:
        """Project each event of the published calendar under its key, in key order.

        An event keeps the key of the link that names it. A new master or single
        event is keyed :func:`event_id`, the Google id it is created under; a new
        override, by its series' key and original start (``instance_key``). Given
        ``keys``, only the events those keys name are read. Integrate writes only the
        events whose projection differs from their link's base. The calendars stream
        pushes nothing: its links are :meth:`CalendarSource.publish`'s.
        """

        if stream.key != EVENTS_STREAM:
            return
        zone = self._zone(stream)
        calendar = self.get_calendar(stream)
        events = apps.get_model("calendar", "CalendarEvent").system_queryset().filter(calendar=calendar)
        if keys is not None:
            events = events.filter(self._named(stream, keys))
        rows = list(events.select_related("series").order_by("pk"))
        pks = {str(row.pk) for row in rows} | {str(row.series_id) for row in rows if row.series_id is not None}
        linked = dict(stream.links.filter(target_object_id__in=pks).values_list("target_object_id", "external_key"))

        def key_of(row: Any) -> str:
            own = linked.get(str(row.pk))
            if own:
                return own
            if row.series_id is None:
                return event_id(row.sqid)
            return instance_key(key_of(row.series), row.original_start)

        for key, row in sorted(((key_of(row), row) for row in rows), key=lambda pair: pair[0]):
            if keys is None or key in keys:
                stamp = row.sqid if row.series_id is None else ""
                yield LocalChange(key, _published(row, zone, stamp=stamp), target=row)

    def _named(self, stream: Any, keys: frozenset[str]) -> Q:
        """Select the events ``keys`` may name, a series' overrides included: bound by a link, or new by their id."""

        names = set(keys) | {series for key in keys if (series := parse_key(key)[0]) is not None}
        bound = stream.links.filter(external_key__in=names).exclude(target_object_id=None)
        named = Q(pk__in=list(bound.values_list("target_object_id", flat=True))) | Q(
            sqid__in=[sqid for name in names if (sqid := _sqid(name))]
        )
        return named | Q(series__in=apps.get_model("calendar", "CalendarEvent").system_queryset().filter(named))

    def write_event(self, link: Any, projection: Any, *, expected_version: str) -> WriteBackResult:
        """Write one published event to Google conditionally; a refusal is integrate's held outbox entry.

        The publishing base admitted the call (``PublishingCalendarBackend.admit_push``). A
        master or single event without a version is created once (:meth:`_create`);
        with one it is patched under ``If-Match``. An override patches its series'
        instance (:meth:`_write_instance`). An event deleted locally is removed
        (:meth:`_remove`). A stale ETag (``412``) is a held conflict
        (``RemoteRejected``), and so is an event Google no longer has: Angee never
        overwrites or recreates a remote change. Google's refusal of this one event
        (``EventRefused``) sets it aside for a retry, and the push goes on with the next.
        """

        stream = link.stream
        client = self._client()
        series, original = parse_key(link.external_key)
        try:
            if projection is None:
                return self._remove(client, link, str(series), original, expected_version)
            if original is not None:
                written = self._write_instance(client, link, projection, str(series), original, expected_version)
            elif expected_version:
                body = _body(projection, instance=False, patch=True)
                written = client.patch(stream.partition, link.external_key, body, etag=expected_version)
            else:
                written = self._create(client, stream, link.external_key, projection)
        except PreconditionFailed:
            raise RemoteRejected() from None
        except EventRefused as refused:
            details = {"status": refused.status, "reason": refused.reason}
            raise RemoteRejected("event_refused", details=details, conflict=False) from None
        if written is None:
            raise RemoteRejected("remote_deleted")
        return WriteBackResult(str(written.get("etag") or ""), canonical_json_sha256(written), {"id": written["id"]})

    def read_written(self, stream: Any, keys: Sequence[str]) -> list[RecordChange]:
        """Read the published events ``keys`` name, each with its ETag; only one Google no longer has is a tombstone.

        Integrate reads them when a person resolves a held conflict: keeping Angee's
        side writes over the version read here, and keeping Google's accepts it. An
        event cancelled in Google is still there, with its ETag, so keeping Angee's
        side writes it back as Angee has it.
        """

        client, zone = self._client(), self._zone(stream)
        written = {
            link.external_key: (link.source_payload or {}).get("id")
            for link in stream.links.filter(external_key__in=list(keys))
        }
        changes = []
        for key in keys:
            series, original = parse_key(key)
            if written.get(key) or original is None:
                item = client.event(stream.partition, str(written.get(key) or key))
            else:
                sent = self._series(stream, str(series))
                item = _instance(client, stream, str(series), original, sent) if sent is not None else None
            if item is None:
                changes.append(RecordChange(key, {}, "", tombstone=True))
                continue
            remote = _remote_published(item, zone, stamped=original is None)
            changes.append(
                RecordChange(
                    key,
                    {"id": item["id"]},
                    canonical_json_sha256(item),
                    remote_version=str(item.get("etag") or ""),
                    mapped_payload=UNSET if remote is None else remote,
                )
            )
        return changes

    def _remove(
        self, client: GoogleCalendarClient, link: Any, series: str, original: datetime | None, expected_version: str
    ) -> WriteBackResult:
        """Remove a published event deleted locally; its link is then a tombstone.

        A master or single event is deleted, and an override's instance restored to
        the series as last written, both under ``If-Match`` (a ``412`` is a held
        conflict). An event, series or instance Google no longer has is already gone.
        """

        if not expected_version:
            raise RemoteRejected("remote_version_missing")
        stream = link.stream
        written = str((link.source_payload or {}).get("id") or link.external_key)
        if original is None:
            client.delete(stream.partition, written, etag=expected_version)
        elif (sent := self._series(stream, series)) is not None:
            restored = _body(_instance_of(sent, original), instance=True, patch=True)
            client.patch(stream.partition, written, restored, etag=expected_version)
        return WriteBackResult("", "", {"id": written}, tombstone=True)

    def _create(self, client: GoogleCalendarClient, stream: Any, key: str, projection: Mapping[str, Any]) -> Any:
        """Create a master or single event once under ``key``; one already there is adopted, or held.

        The stamp lookup runs first, then the insert under the chosen id, so a crash
        between Google's create and the link's record makes no second event: the
        retry finds the first by its stamp, or by its taken id (``409``). It is
        adopted when it is the one event carrying this event's stamp and holds what
        Angee sends; anything else is a held conflict. A series' instances carry its
        stamp too: only masters and single events are counted.
        """

        stamped = client.stamped(stream.partition, STAMP, projection["stamp"])
        found = [item for item in stamped if not item.get("recurringEventId")]
        if not found:
            try:
                return client.insert(stream.partition, {"id": key, **_body(projection, instance=False, patch=False)})
            except EventIdTaken:
                found = [item for item in (client.event(stream.partition, key),) if item is not None]
        mine = [item for item in found if item.get("id") == key and _stamp(item) == projection["stamp"]]
        if len(found) != 1 or not mine:
            raise RemoteRejected("remote_id_taken")
        if _remote_published(mine[0], self._zone(stream), stamped=True) != projection:
            raise RemoteRejected()
        return mine[0]

    def _write_instance(
        self,
        client: GoogleCalendarClient,
        link: Any,
        projection: Mapping[str, Any],
        series: str,
        original: datetime,
        expected_version: str,
    ) -> Any:
        """Patch the instance of the series ``series`` at ``original`` an override names.

        Its first write reads the instance. One that already holds this write (an
        earlier attempt landed before its answer was lost; for a cancelled override,
        its status alone) is adopted. One Google holds unlike the series as Angee
        last wrote it is a remote edit, held. Later writes patch the instance id it
        returned under ``If-Match``. A series not written yet, or without that
        instance, refuses for now.
        """

        stream = link.stream
        written = (link.source_payload or {}).get("id") if expected_version else None
        body = _body(projection, instance=True, patch=True)
        if written:
            return client.patch(stream.partition, written, body, etag=expected_version)
        sent = self._series(stream, series)
        if sent is None:
            raise RemoteRejected("series_unavailable", conflict=False)
        item = _instance(client, stream, series, original, sent)
        if item is None:
            raise RemoteRejected("instance_unavailable", conflict=False)
        remote = _remote_published(item, self._zone(stream), stamped=False)
        if projection["status"] == EventStatus.CANCELLED:
            landed = item.get("status") == "cancelled"
        else:
            landed = remote == projection
        if landed:
            return item
        if remote != _instance_of(sent, original):
            raise RemoteRejected()
        return client.patch(stream.partition, str(item["id"]), body, etag=str(item.get("etag") or ""))

    def _series(self, stream: Any, series: str) -> dict[str, Any] | None:
        """Return the projection the series ``series`` was last written with, or ``None`` before its first write."""

        link = stream.links.filter(external_key=series).select_related("applied_base").first()
        base = link.applied_base if link is not None else None
        return dict(base.mapped_payload) if base is not None and base.mapped_payload else None

    def _zone(self, stream: Any) -> str:
        """Return the zone the published calendar keeps: its own, else the project's."""

        return self.get_calendar(stream).timezone or settings.TIME_ZONE


def _instance(
    client: GoogleCalendarClient, stream: Any, series: str, original: datetime, sent: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Read the instance at ``original`` of the series ``series`` (last written as ``sent``), or ``None``."""

    day_zone = sent["timezone"] if sent["all_day"] else None
    return client.instance(stream.partition, series, original, all_day_zone=day_zone)


def event_id(sqid: str) -> str:
    """Return the Google event id a new published event is created under: its public id in lowercase base32hex.

    Google takes a client-chosen id of 5 to 1024 characters from ``a``-``v`` and
    ``0``-``9``. The id is the event's link key, so every retry creates under it.
    """

    return base64.b32hexencode(sqid.encode()).decode().rstrip("=").lower()


def _sqid(key: str) -> str:
    """Return the public id an :func:`event_id` key was made from, or ``""`` for any other key."""

    try:
        return base64.b32hexdecode(key.upper() + "=" * (-len(key) % 8)).decode("ascii")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return ""


def _published(event: Any, zone: str, *, stamp: str = "") -> dict[str, Any]:
    """Return what publishing writes of one event, the shape both sides of a push compare.

    ``event`` is a stored ``CalendarEvent`` or a :class:`ParsedEvent` read from
    Google: calendar's column projection (``event_columns``) of the columns Google
    keeps, its rule in the form icalendar reads it back (``canonical_rule``), its zone
    and its stamp. ``zone`` is the calendar's (the project's when blank): an event
    without a zone of its own keeps it, since Google expands a series in a named
    zone. ``stamp`` is a master's or single event's public id; an instance has none.
    Attendees and the conference link are not published.
    """

    return {
        **event_columns(event, _PUBLISHED_COLUMNS),
        "recurrence": canonical_rule(event.recurrence),
        "timezone": event.timezone or zone,
        "stamp": stamp,
    }


def _remote_published(item: Mapping[str, Any], zone: str, *, stamped: bool) -> dict[str, Any] | None:
    """Return a Google event as :func:`_published` shapes it, or ``None`` once it is cancelled or unreadable.

    The sync-in mapping reads it (:func:`_event`); ``stamped`` reads its
    ``angee_event`` stamp, which only a master or single event carries.
    """

    try:
        _key, event = _event(item, zone, zone)
    except _UNREADABLE:  # An event Angee cannot read is not one Angee wrote.
        return None
    if event is None:
        return None
    return _published(event, zone, stamp=_stamp(item) if stamped else "")


def _instance_of(series: Mapping[str, Any], original: datetime) -> dict[str, Any]:
    """Return the projection a series (as last written) gives its unchanged instance at ``original``.

    The instance ends where the event owner's arithmetic puts it
    (``CalendarEvent.span_end``: an all-day instance spans local days).
    """

    end = apps.get_model("calendar", "CalendarEvent").span_end(
        datetime.fromisoformat(series["start"]),
        datetime.fromisoformat(series["end"]),
        all_day=series["all_day"],
        at=original,
        zone=series["timezone"],
    )
    return {
        **series,
        "start": original.astimezone(UTC).isoformat(),
        "end": end.astimezone(UTC).isoformat(),
        "recurrence": "",
        "stamp": "",
    }


def _body(published: Mapping[str, Any], *, instance: bool, patch: bool) -> dict[str, Any]:
    """Return the Google event fields a projection writes (:func:`_published`).

    Times carry the projection's zone (a ``date`` for an all-day event). A patch
    also clears the other time key (``null``), since Google merges a patched
    ``start`` into the one it holds: an event switched between all-day and timed
    keeps one. A master or single event states its rule (an empty ``recurrence``
    clears one) and its stamp; an instance states neither.
    """

    zone = published["timezone"]

    def moment(name: str) -> dict[str, str | None]:
        value = datetime.fromisoformat(published[name])
        if published["all_day"]:
            kept, cleared, written = "date", "dateTime", _local_day(value, zone).isoformat()
        else:
            kept, cleared, written = "dateTime", "date", value.isoformat()
        return {kept: written, **({cleared: None} if patch else {}), "timeZone": zone}

    body: dict[str, Any] = {
        "summary": published["title"],
        "description": published["description"],
        "location": published["location"],
        "start": moment("start"),
        "end": moment("end"),
        "status": _WRITTEN_STATUSES[published["status"]],
        "transparency": _WRITTEN_TRANSPARENCIES[published["transparency"]],
        "visibility": _WRITTEN_VISIBILITIES[published["visibility"]],
    }
    if not instance:
        body["recurrence"] = [f"RRULE:{published['recurrence']}"] if published["recurrence"] else []
        body["extendedProperties"] = {"private": {STAMP: published["stamp"]}}
    return body


def _stamp(item: Mapping[str, Any]) -> str:
    """Return the ``angee_event`` stamp a Google event carries, or ``""``."""

    return str(((item.get("extendedProperties") or {}).get("private") or {}).get(STAMP) or "")


@dataclass(frozen=True)
class _GooglePage:
    """One mapped ``events.list`` page: the stream (calendar, generation) and request that read it, and its answer."""

    partition: str
    generation: int
    request: dict[str, Any]
    payloads: dict[str, dict[str, Any] | None]
    following: dict[str, Any]


def _event(item: Mapping[str, Any], zone: str, series_zone: str) -> tuple[str, ParsedEvent | None]:
    """Map one Google event onto its key and neutral event; ``None`` for a removed master or single event.

    ``zone`` is the calendar's; ``series_zone`` the zone an exception's series
    expands in, where its all-day original start is read.
    """

    google_id = str(item["id"])
    master_id = str(item.get("recurringEventId") or "")
    cancelled = item.get("status") == "cancelled"
    if not master_id:
        key = series_key(google_id)
        if cancelled:
            return key, None
        original = None
    else:
        original = _moment(item["originalStartTime"], series_zone)
        key = instance_key(master_id, original)
    if cancelled and "start" not in item:
        # Google sends a cancelled exception as its identity alone: it keeps its original place.
        assert original is not None  # A removed master or single event returned above.
        all_day = "date" in item["originalStartTime"]
        local = _known_zone(item["originalStartTime"].get("timeZone")) or series_zone
        end = _moment({"date": _local_day(original, local) + timedelta(days=1)}, local) if all_day else original
        return key, ParsedEvent(
            title="",
            start=original,
            end=end,
            all_day=all_day,
            status=EventStatus.CANCELLED,
            transparency=EventTransparency.BUSY,
            visibility=EventVisibility.DEFAULT,
            series=series_key(master_id),
            original_start=original,
        )
    start_prop, end_prop = item["start"], item.get("end") or item["start"]
    event_zone = _known_zone(start_prop.get("timeZone"))
    local = event_zone or zone
    start = _moment(start_prop, local)
    end = _moment(end_prop, local)
    all_day = "date" in start_prop
    if all_day and end <= start:
        end = _moment({"date": date.fromisoformat(str(start_prop["date"])) + timedelta(days=1)}, local)
    return key, ParsedEvent(
        title=str(item.get("summary") or ""),
        description=str(item.get("description") or ""),
        location=str(item.get("location") or ""),
        start=start,
        end=end,
        all_day=all_day,
        recurrence="" if master_id else series_rule(_recurrence(item)),
        timezone=event_zone,
        series=series_key(master_id) if master_id else "",
        original_start=original,
        status=_STATUSES.get(str(item.get("status")), EventStatus.CONFIRMED),
        transparency=_TRANSPARENCIES.get(str(item.get("transparency")), EventTransparency.BUSY),
        visibility=_VISIBILITIES.get(str(item.get("visibility")), EventVisibility.DEFAULT),
        conference_url=_conference_url(item),
        attendees=_attendees(item),
    )


def _own_key(item: Mapping[str, Any], zone: str) -> str:
    """Return the key an unmappable event is quarantined under: its own instance key when it is one.

    ``zone`` is the zone its series expands in.
    """

    master_id = str(item.get("recurringEventId") or "")
    try:
        if master_id:
            return instance_key(master_id, _moment(item["originalStartTime"], zone))
    except _UNREADABLE:  # An unreadable instance keeps a key of its own Google id.
        pass
    return series_key(str(item["id"]))


def _moment(value: Mapping[str, Any], zone: str) -> datetime:
    """Return a Google ``date`` (local midnight) or ``dateTime`` (its instant) as aware UTC.

    A ``date``, and a ``dateTime`` without an offset, read in the value's own
    ``timeZone``, else ``zone``: for an original start, the zone its series
    expands in (the master's, else the calendar's).
    """

    local = _known_zone(value.get("timeZone")) or zone
    if "dateTime" not in value:
        day = value["date"]
        day = day if isinstance(day, date) else date.fromisoformat(str(day))
        return timezone.make_aware(datetime.combine(day, time.min), get_zone_info(local)).astimezone(UTC)
    moment = datetime.fromisoformat(str(value["dateTime"]).replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = timezone.make_aware(moment, get_zone_info(local))
    return moment.astimezone(UTC)


def _local_day(moment: datetime, zone: str) -> date:
    """Return the local date of ``moment`` in ``zone``."""

    return moment.astimezone(get_zone_info(zone)).date()


def _known_zone(name: Any) -> str:
    """Return ``name`` when it is a zone the project knows, else ``""`` (the calendar's)."""

    text = str(name or "")
    return text if text and text in get_zone_names() else ""


def _series_zone(master: Mapping[str, Any]) -> str:
    """Return the zone a Google series expands in: its master's ``start.timeZone``, else ``""`` (the calendar's)."""

    return _known_zone((master.get("start") or {}).get("timeZone"))


def _recurrence(item: Mapping[str, Any]) -> Component:
    """Return a Google event's ``recurrence`` lines (RFC 5545 ``RRULE``, ``EXDATE``, ``RDATE``) parsed as one VEVENT."""

    lines = [str(line).strip() for line in item.get("recurrence") or ()]
    return Event.from_ical("\r\n".join(["BEGIN:VEVENT", *lines, "END:VEVENT"]))


def _instant(value: date | datetime, tzid: str, zone: str) -> datetime:
    """Return one ``EXDATE`` value as aware UTC.

    A date is local midnight in ``zone`` (the series' zone); a time with a ``TZID``
    the project knows reads its wall clock there; a UTC time keeps its instant; a
    floating time, or one in a zone the project does not know, reads in ``zone``.
    """

    if not isinstance(value, datetime):
        return _moment({"date": value}, zone)
    if not tzid and value.tzinfo is not None:
        return value.astimezone(UTC)
    local = _known_zone(tzid) or zone
    return timezone.make_aware(value.replace(tzinfo=None), get_zone_info(local)).astimezone(UTC)


def _conference_url(item: Mapping[str, Any]) -> str:
    """Return the event's video entry point, else its ``hangoutLink``, else ``""``."""

    for entry in (item.get("conferenceData") or {}).get("entryPoints") or ():
        if entry.get("entryPointType") == "video" and entry.get("uri"):
            return str(entry["uri"])
    return str(item.get("hangoutLink") or "")


def _attendees(item: Mapping[str, Any]) -> tuple[ParsedAttendee, ...]:
    """Return the people invited, the event's ``organizer`` first (``organizer_first``); rooms are not people.

    An entry Google marks ``resource`` (a room or equipment) is no attendee; any
    other is invited as required, or optional.
    """

    listed = [
        ParsedAttendee(
            address=str(attendee.get("email") or "").strip(),
            role=AttendeeRole.OPTIONAL if attendee.get("optional") else AttendeeRole.REQUIRED,
            response=_RESPONSES.get(str(attendee.get("responseStatus")), AttendeeResponse.NEEDS_ACTION),
            display_name=str(attendee.get("displayName") or ""),
        )
        for attendee in item.get("attendees") or ()
        if not attendee.get("resource")
    ]
    organizer = item.get("organizer") or {}
    return organizer_first(
        listed, str(organizer.get("email") or "").strip(), name=str(organizer.get("displayName") or "")
    )

