"""Google Calendar sync-in: each calendar of a connected Google account, synced with Google's sync tokens.

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

The calendars are synced, not locally writable: the backend is read-only
(``writable`` stays false) and reads with ``calendar.readonly``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from time import monotonic
from typing import Any, ClassVar

from django.core.exceptions import ValidationError
from django.utils import timezone
from icalendar import Component, Event
from rebac import system_context

from angee.calendar.backends import (
    CalendarBackend,
    ParsedAttendee,
    ParsedCalendar,
    ParsedEvent,
    cancelled_instances,
    excluded_dates,
    instance_key,
    organizer_first,
    parse_key,
    series_key,
    series_rule,
)
from angee.calendar.choices import AttendeeResponse, AttendeeRole, EventStatus, EventTransparency, EventVisibility
from angee.calendar_integrate_google.client import MAX_PAGE_SIZE, GoogleCalendarClient, SyncTokenExpired
from angee.integrate.credentials import CredentialKind
from angee.integrate.errors import IntegrationError
from angee.integrate.streams import CursorInvalid, StreamPage
from angee.scheduling.zones import get_zone_info, get_zone_names

CALENDAR_READONLY = "https://www.googleapis.com/auth/calendar.readonly"
"""The OAuth scope sync-in reads every calendar of the account with."""

_SWEEP_PAGE = 250
"""Events per page of a sweep's full listing: well under the transport's 8 MiB answer cap."""

_STATUSES = {"confirmed": EventStatus.CONFIRMED, "tentative": EventStatus.TENTATIVE}


def _sweep(stream: Any) -> tuple[Any, ...]:
    """Return the identity of the absence sweep running on ``stream``: its calendar, generation and start."""

    started = (stream.reconcile_state or {}).get("started_at")
    return (stream.partition, stream.generation, started) if started else ()
_VISIBILITIES = {
    "public": EventVisibility.PUBLIC,
    "private": EventVisibility.PRIVATE,
    "confidential": EventVisibility.PRIVATE,
}
_RESPONSES = {
    "accepted": AttendeeResponse.ACCEPTED,
    "declined": AttendeeResponse.DECLINED,
    "tentative": AttendeeResponse.TENTATIVE,
}


class GoogleCalendarBackend(CalendarBackend):
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
        """Admit only an OAuth credential of the ``google`` client whose grant covers :attr:`oauth_scopes`."""

        if credential.kind == CredentialKind.OAUTH:
            with system_context(reason="calendar_integrate_google.admit_credential"):
                client = credential.oauth_client
            if (
                client is not None
                and client.slug == self.oauth_client
                and set(self.oauth_scopes) <= set(credential.granted_scopes or ())
            ):
                return
        raise ValidationError(
            {"credential": "This account cannot read Google calendars; connect it with Calendar access."}
        )

    # --- calendars ---

    def discover(self) -> list[ParsedCalendar]:
        """Return the account's calendars from its calendar list, read once per adapter."""

        if self._calendars is None:
            names = get_zone_names()
            calendars = []
            for entry in self._client().calendar_list():
                self._require_time()
                if entry.get("id"):
                    calendars.append(
                        ParsedCalendar(
                            key=str(entry["id"]),
                            name=str(entry.get("summaryOverride") or entry.get("summary") or ""),
                            timezone=_known_zone(entry.get("timeZone"), names),
                            color=str(entry.get("backgroundColor") or ""),
                        )
                    )
            self._calendars = calendars
        return self._calendars

    # --- events ---

    def extract_events(self, stream: Any, page_bound: int, *, deadline: float | None = None) -> StreamPage:
        """Page one ``events.list`` page in key order; the token that follows it commits with its last record.

        A Google page may map to more records than ``page_bound`` (a master's
        ``EXDATE``s): while records remain the cursor names the Google request
        (``from``) and the last key paged (``after``), and an adapter that no longer
        holds that page asks Google for it again. A removed event that was never
        linked is left out.
        """

        cursor = dict(stream.cursor or {})
        request = dict(cursor.get("from") or cursor)
        after = cursor.get("after")
        held = self._held
        if held is None or (held.partition, held.request) != (stream.partition, request):
            # One adapter may drain several calendars of the source: a held page is one calendar's.
            held = self._held = self._fetch(stream, request, page_bound)
        keys = sorted(key for key in held.payloads if after is None or key > after)
        batch = keys[: max(1, page_bound)]
        records = self.record_changes(stream, {key: held.payloads[key] for key in batch})
        if len(keys) > len(batch):
            return StreamPage(records, {"from": request, "after": batch[-1]}, exhausted=False)
        return StreamPage(records, held.following, exhausted="page_token" not in held.following)

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
            item = client.instance(stream.partition, master_id, _original_start(master, moment, zone))
            observed = dict(self._observe_item(master, zone))
            if item is not None:
                observed.update(self._observe_item(item, zone))
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
                for item in page.get("items") or ():
                    payloads.update(self._observe_item(item, zone))
                token = str(page.get("nextPageToken") or "")
                if not token:
                    break
            self._listing = (sweep, payloads)
        return sorted(key for key, payload in self._listing[1].items() if payload is not None)

    def _require_time(self) -> None:
        """Stop a listing that reaches the cycle's deadline: the next sync lists again."""

        if self.deadline is not None and monotonic() > self.deadline:
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
                max_results=min(page_bound, MAX_PAGE_SIZE),
            )
        except SyncTokenExpired:
            raise CursorInvalid() from None
        zone = self.get_calendar(stream).timezone
        payloads: dict[str, dict[str, Any] | None] = {}
        for item in page.get("items") or ():
            payloads.update(self._observe_item(item, zone))
        removed = [key for key, payload in payloads.items() if payload is None]
        if removed:
            linked = set(stream.links.filter(external_key__in=removed).values_list("external_key", flat=True))
            payloads = {key: payload for key, payload in payloads.items() if payload is not None or key in linked}
        following = (
            {"page_token": str(page["nextPageToken"]), **({"sync_token": sync_token} if sync_token else {})}
            if page.get("nextPageToken")
            else {"sync_token": str(page.get("nextSyncToken") or "")}
        )
        return _GooglePage(stream.partition, dict(request), payloads, following)

    def _observe_item(self, item: Mapping[str, Any], zone: str) -> list[tuple[str, dict[str, Any] | None]]:
        """Map one Google event to its keyed payload, then a master's ``EXDATE`` instances.

        A removed master or single event is ``None``; an event the calendar cannot
        take is a quarantine payload under its own key.
        """

        event_id = str(item.get("id") or "")
        if not event_id:
            return []
        try:
            key, event = _event(item, zone)
            if event is None:
                return [(key, None)]
            observed: list[tuple[str, ParsedEvent]] = [(key, event)]
            if event.recurrence:
                local = event.timezone or zone
                exdates = [_instant(value, tzid, local) for value, tzid in excluded_dates(_recurrence(item))]
                observed.extend(cancelled_instances(event, event_id, exdates, calendar_zone=zone))
        except Exception:  # noqa: BLE001 — the driver quarantines semantic input per record.
            return [(_own_key(item, zone), {"error": "invalid_event"})]
        return [(key, self.observe(event)) for key, event in observed]

    def _client(self) -> GoogleCalendarClient:
        """Return a client reading with the source's credential, reloaded for this unit of work."""

        credential = self.bridge.fresh_credential()
        if credential is None:
            raise IntegrationError("This Google calendar source has no connected account.")
        return GoogleCalendarClient(credential, self.oauth_scopes)


@dataclass(frozen=True)
class _GooglePage:
    """One mapped ``events.list`` page: the calendar and request that read it, its payloads, and the cursor after it."""

    partition: str
    request: dict[str, Any]
    payloads: dict[str, dict[str, Any] | None]
    following: dict[str, Any]


def _event(item: Mapping[str, Any], zone: str) -> tuple[str, ParsedEvent | None]:
    """Map one Google event onto its key and neutral event; ``None`` for a removed master or single event."""

    event_id = str(item["id"])
    master_id = str(item.get("recurringEventId") or "")
    cancelled = item.get("status") == "cancelled"
    if not master_id:
        key = series_key(event_id)
        if cancelled:
            return key, None
        original = None
    else:
        original = _moment(item["originalStartTime"], zone)
        key = instance_key(master_id, original)
    if cancelled and "start" not in item:
        # Google sends a cancelled exception as its identity alone: it keeps its original place.
        assert original is not None  # A removed master or single event returned above.
        all_day = "date" in item["originalStartTime"]
        end = _moment({"date": _local_day(original, zone) + timedelta(days=1)}, zone) if all_day else original
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
    event_zone = _known_zone(start_prop.get("timeZone"), get_zone_names())
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
        status=EventStatus.CANCELLED if cancelled else _STATUSES.get(str(item.get("status")), EventStatus.CONFIRMED),
        transparency=EventTransparency.FREE if item.get("transparency") == "transparent" else EventTransparency.BUSY,
        visibility=_VISIBILITIES.get(str(item.get("visibility")), EventVisibility.DEFAULT),
        conference_url=_conference_url(item),
        attendees=_attendees(item),
    )


def _own_key(item: Mapping[str, Any], zone: str) -> str:
    """Return the key an unmappable event is quarantined under: its own instance key when it is one."""

    master_id = str(item.get("recurringEventId") or "")
    try:
        if master_id:
            return instance_key(master_id, _moment(item["originalStartTime"], zone))
    except Exception:  # noqa: BLE001 — an unreadable instance keeps a key of its own Google id.
        pass
    return series_key(str(item["id"]))


def _moment(value: Mapping[str, Any], zone: str) -> datetime:
    """Return a Google ``date`` (local midnight in ``zone``) or ``dateTime`` (its instant) as aware UTC.

    A ``dateTime`` without an offset is read in its own ``timeZone``, else ``zone``.
    """

    if "dateTime" not in value:
        day = value["date"]
        day = day if isinstance(day, date) else date.fromisoformat(str(day))
        return timezone.make_aware(datetime.combine(day, time.min), get_zone_info(zone)).astimezone(UTC)
    moment = datetime.fromisoformat(str(value["dateTime"]).replace("Z", "+00:00"))
    if moment.tzinfo is None:
        own = _known_zone(value.get("timeZone"), get_zone_names()) or zone
        moment = timezone.make_aware(moment, get_zone_info(own))
    return moment.astimezone(UTC)


def _local_day(moment: datetime, zone: str) -> date:
    """Return the local date of ``moment`` in ``zone``."""

    return moment.astimezone(get_zone_info(zone)).date()


def _original_start(master: Mapping[str, Any], moment: datetime, zone: str) -> str:
    """Return the ``originalStart`` Google reads one instance of ``master`` by: a date for an all-day series."""

    if "date" in (master.get("start") or {}):
        own = _known_zone((master.get("start") or {}).get("timeZone"), get_zone_names()) or zone
        return _local_day(moment, own).isoformat()
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _known_zone(name: Any, names: Iterable[str]) -> str:
    """Return ``name`` when it is a zone the project knows, else ``""`` (the calendar's)."""

    text = str(name or "")
    return text if text and text in names else ""


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
    local = _known_zone(tzid, get_zone_names()) or zone
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

