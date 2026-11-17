"""Calendar source mapping: integrate owns the loop and the calendar owns its calendars and events.

A source has two kinds of stream, both pull-only record replicas:

- ``calendars``: one record per remote calendar the backend discovers
  (:class:`ParsedCalendar`), keyed by its remote key. Its ``RecordLink`` names
  the mirrored ``Calendar`` and the source states that calendar's identity
  (``CalendarManager.mirror``), so a person's rename outranks a remote one.
- ``events``, one per remote calendar, partitioned by that calendar's key: one
  record per remote event (:class:`ParsedEvent`). A master's key is its UID and an
  instance's the UID plus its ``RECURRENCE-ID`` instant (:func:`instance_key`).
  Every write composes the calendar owners (``CalendarEventManager.ingest`` and
  ``CalendarEvent.withdraw``).

Both sides of each three-way sync compare one deterministic projection: an event's
(:func:`event_projection`), and for a calendar the identity this source stated, so
a person's own edit is no local change of the source's. Remote identity lives only
in the streams' ``RecordLink`` rows. Extraction pages what the source serves;
integrate's absence sweep finds what it stopped serving (:meth:`CalendarBackend.enumerate_keys`,
:meth:`CalendarBackend.on_absent`). The manual backend declares no streams.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar

from django.apps import apps

from angee.base.serialization import canonical_json_sha256
from angee.integrate.errors import IntegrationError
from angee.integrate.http import HttpClientMixin
from angee.integrate.impl import BridgeImpl
from angee.integrate.models import IntegrationLifecycle
from angee.integrate.states import LinkStatus, StreamDefinition, SyncFailureKind
from angee.integrate.streams import ApplyResult, RecordChange, SemanticError, StreamPage

CALENDARS_STREAM = "calendars"
"""The stream of a source's remote calendars: one record per calendar, linked to its ``Calendar``."""

EVENTS_STREAM = "events"
"""The stream of one remote calendar's events, partitioned by the calendar's key."""

EVENT_FIELDS = (
    "title",
    "description",
    "location",
    "start",
    "end",
    "all_day",
    "recurrence",
    "timezone",
    "status",
    "transparency",
    "visibility",
    "conference_url",
)
"""An event's own columns: what a person edits and a source writes, carried by :class:`ParsedEvent`.

An event's place in a series (``series``, ``original_start``) is set when it is
inserted, and its attendees are rows of their own.
"""

_PROJECTED = (*EVENT_FIELDS, "original_start")
"""The columns an event's comparison projection carries, with its attendees."""

_MOMENTS = frozenset({"start", "end", "original_start"})
"""The projected columns that are moments, carried as UTC ISO-8601 text."""


@dataclass(frozen=True)
class ParsedCalendar:
    """A remote calendar: its key in the source and the identity the source states.

    ``key`` is stable across syncs; it is the calendars-stream record key and the
    events stream partition. ``timezone`` is an IANA name, or blank for the project
    zone: the calendar expands its recurring events in it.
    """

    key: str
    name: str
    timezone: str = ""
    color: str = ""

    def payload(self) -> dict[str, Any]:
        """Return the retained source payload of this calendar: its stated identity."""

        return {"calendar": {"name": self.name, "color": self.color, "timezone": self.timezone}}


@dataclass(frozen=True)
class ParsedAttendee:
    """One invited address of a parsed event, with its role and RSVP in the calendar's vocabulary.

    ``address`` is as the source gave it; dedup and the projection read the handle
    owner's normalized form (:func:`normalized_address`).
    """

    address: str
    role: str
    response: str
    display_name: str = ""


@dataclass(frozen=True)
class ParsedEvent:
    """One event parsed from a source, neutral of the wire format.

    Moments are aware UTC; an all-day event starts at local midnight in its
    calendar's zone. ``timezone`` is the IANA zone the event keeps (its DTSTART
    zone), or blank for its calendar's. ``series`` is the external key of the
    recurring master an instance overrides and ``original_start`` the instance it
    replaces; both are blank on a master or a single event. Field values use the
    calendar's vocabulary (``EventStatus``, ``EventTransparency``, ``EventVisibility``).
    """

    title: str
    start: datetime
    end: datetime
    all_day: bool
    status: str
    transparency: str
    visibility: str
    description: str = ""
    location: str = ""
    recurrence: str = ""
    timezone: str = ""
    series: str = ""
    original_start: datetime | None = None
    conference_url: str = ""
    attendees: tuple[ParsedAttendee, ...] = ()


_INSTANT_SUFFIX = len("#YYYYMMDDTHHMMSSZ")
"""The length an instance's ``RECURRENCE-ID`` instant adds to its series key."""


def series_key(uid: str) -> str:
    """Return the external key of the series or single event ``uid``: the UID, or its digest when too long.

    A link's external key is bounded; a UID that leaves no room for an instance's
    suffix is keyed by its SHA-256, so one oversized UID never fails its page.
    """

    limit = apps.get_model("integrate", "RecordLink")._meta.get_field("external_key").max_length or 0
    if len(uid) + _INSTANT_SUFFIX <= limit:
        return uid
    return "sha256:" + hashlib.sha256(uid.encode()).hexdigest()


def basic_instant(moment: datetime) -> str:
    """Return a moment as UTC ISO-8601 basic: an occurrence id's and an instance key's suffix."""

    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def instance_key(uid: str, original_start: datetime) -> str:
    """Return the external key of one instance of the series ``uid``: its series key plus its UTC instant."""

    return f"{series_key(uid)}#{basic_instant(original_start)}"


def normalized_address(address: str) -> str:
    """Return the ``parties.Handle`` owner's normalized form of an attendee address, ``""`` for none."""

    handles: Any = apps.get_model("parties", "Handle")
    return str(handles.normalize_value(handles.Platform.for_value(address), address) or "")


def attendee_identity(handle: Any | None, address: str) -> str:
    """Return what an attendee compares on: the handle it lands on, or its address before that handle exists.

    ``handle`` is the canonical handle ``HandleManager.find_identity`` finds for the
    address (``CalendarEventManager.attendee_handles``). A stored attendee row
    compares on its handle the same way, so a merged pair of handles is one
    attendee on both sides.
    """

    return str(handle.pk) if handle is not None else f"new:{normalized_address(address)}"


def _columns(event: ParsedEvent) -> dict[str, Any]:
    """Return the event's projected columns as JSON: moments as UTC ISO-8601 text."""

    def value(name: str) -> Any:
        found = getattr(event, name)
        return found.astimezone(UTC).isoformat() if name in _MOMENTS and found is not None else found

    return {name: value(name) for name in _PROJECTED}


def event_projection(event: ParsedEvent, identities: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Return the JSON comparison shape a remote event and a stored row share.

    Attendees compare as a set of ``(identity, role, response)``
    (:func:`attendee_identity`): ``identities`` maps each remote address to its
    identity, and a stored attendee's address already is one. Of two addresses on
    one handle the first is kept, as ingest keeps it. Display names belong to the
    handle. The series key stays out of it: an instance's own key already names
    its series.
    """

    attendees: dict[str, list[str]] = {}
    for attendee in event.attendees:
        identity = identities[attendee.address] if identities is not None else attendee.address
        attendees.setdefault(identity, [identity, attendee.role, attendee.response])
    return {**_columns(event), "attendees": sorted(attendees.values())}


def event_payload(event: ParsedEvent) -> dict[str, Any]:
    """Return the JSON source payload a stream retains for one remote event.

    Its projected columns, its series key, and its attendees as the source gave
    them: the addresses ingest lands on handles. It names no handle, so its hash
    holds while handles merge.
    """

    return {
        "event": _columns(event),
        "series": event.series,
        "attendees": [
            [attendee.address, attendee.role, attendee.response, attendee.display_name] for attendee in event.attendees
        ],
    }


def event_from_payload(payload: Mapping[str, Any]) -> ParsedEvent:
    """Decode a retained :func:`event_payload` for the calendar's ingest verb."""

    event = payload["event"]

    def value(name: str) -> Any:
        found = event[name]
        return datetime.fromisoformat(found) if name in _MOMENTS and found else found

    return ParsedEvent(
        **{name: value(name) for name in _PROJECTED},
        series=payload["series"],
        attendees=tuple(
            ParsedAttendee(address=address, role=role, response=response, display_name=name)
            for address, role, response, name in payload["attendees"]
        ),
    )


@dataclass
class _EventsPage:
    """One events page's locked work: its calendar, the rows its links name, and its attendees' handles.

    Rows and links are by external key; handles by the address the source gave.
    """

    calendar: Any
    rows: dict[str, Any] = field(default_factory=dict)
    links: dict[str, Any] = field(default_factory=dict)
    handles: dict[str, Any] = field(default_factory=dict)


def _attendee_addresses(payloads: Iterable[Mapping[str, Any] | None]) -> set[str]:
    """Return the attendee addresses the mappable event payloads name."""

    return {
        attendee[0]
        for payload in payloads
        if payload is not None and "error" not in payload
        for attendee in payload.get("attendees", ())
    }


class CalendarBackend(BridgeImpl, HttpClientMixin):
    """A source's calendars stream and one events stream per calendar, applied through the calendar owners.

    A concrete backend implements :meth:`discover`, :meth:`extract_events`,
    :meth:`read_events` and :meth:`event_keys`; it builds its event pages with
    :meth:`observe` and :meth:`page`, and this base extracts the calendars stream,
    applies every record and applies integrate's absence sweep (:meth:`on_absent`).
    ``writable`` declares whether the source takes local edits: a read-only
    backend's calendars refuse them (``Calendar.is_writable``).
    """

    registry_setting = "ANGEE_CALENDAR_SOURCE_BACKEND_CLASSES"

    category = "calendar"
    label = "Calendar"
    icon = "calendar"

    writable: ClassVar[bool] = False
    """Whether calendars of this backend take local edits; a read-only subscription does not."""

    sync_parallelism: ClassVar[int | None] = 1
    """Drain one partition at a time, in ``sync_bridge``'s (key, partition) order.

    ``calendars`` sorts before ``events``, so a calendar is mirrored before its
    events write to it. Integrate has no ordering between streams yet; this is the
    explicit reliance, and with ``CalendarSource.calendar_for_partition`` it is
    the interim one: both go when an events stream names its calendar's link
    (``SyncStream.container``). A drain that meets an unmirrored calendar fails its
    partition transiently (:meth:`get_calendar`) and retries on the next cycle.
    """

    supports_identity_reads: ClassVar[bool] = True
    """Both stream kinds re-read identities (:meth:`read_keys`), so a retried or held record reads its source."""

    _page: _EventsPage | None = None

    def discover(self) -> list[ParsedCalendar]:
        """Return the remote calendars this source serves, each with its stable key."""

        raise NotImplementedError("CalendarBackend subclasses must implement discover().")

    def extract_events(self, stream: Any, page_bound: int, *, deadline: float | None = None) -> StreamPage:
        """Extract one page of the events of the remote calendar ``stream.partition`` names."""

        raise NotImplementedError("CalendarBackend subclasses must implement extract_events().")

    def read_events(self, stream: Any, keys: Sequence[str]) -> dict[str, dict[str, Any] | None]:
        """Read the current payload of each requested event key of ``stream``, ``None`` for one the source lacks."""

        raise NotImplementedError("CalendarBackend subclasses must implement read_events().")

    def event_keys(self, stream: Any) -> Iterable[str]:
        """Return every event key the remote calendar ``stream.partition`` serves now."""

        raise NotImplementedError("CalendarBackend subclasses must implement event_keys().")

    def read_keys(self, stream: Any, keys: Sequence[str]) -> list[RecordChange]:
        """Re-read the requested identities, each once, a missing one as a tombstone (integrate's identity reads)."""

        if stream.key == CALENDARS_STREAM:
            remotes = {remote.key: remote.payload() for remote in self.discover()}
            payloads: Mapping[str, Any] = {key: remotes.get(key) for key in keys}
        else:
            payloads = self.read_events(stream, keys)
        return self.record_changes(stream, {key: payloads.get(key) for key in keys})

    def enumerate_keys(self, stream: Any, *, after: str | None = None) -> list[str]:
        """Return the keys the source serves now, in key order after ``after``: integrate's absence sweep reads them."""

        if stream.key == CALENDARS_STREAM:
            keys: Iterable[str] = (remote.key for remote in self.discover())
        else:
            keys = self.event_keys(stream)
        return sorted(key for key in set(keys) if after is None or key > after)

    def streams(self, *, deadline: float | None = None) -> Iterable[StreamDefinition]:
        """Declare the calendars stream, then each discovered calendar's events stream."""

        yield self.bridge.calendars_stream()
        for remote in sorted(self.discover(), key=lambda item: item.key):
            yield self.bridge.events_stream(remote)

    def extract(self, stream: Any, page_bound: int, *, deadline: float | None = None) -> StreamPage:
        """Extract the calendars stream here; an events stream through the backend's :meth:`extract_events`."""

        if stream.key != CALENDARS_STREAM:
            return self.extract_events(stream, page_bound, deadline=deadline)
        cursor = dict(stream.cursor or {})
        payloads = {remote.key: remote.payload() for remote in self.discover()}
        return self.page(stream, payloads, page_bound, after=cursor.get("after"), cursor={"listed": True})

    def get_calendar(self, stream: Any) -> Any:
        """Return the calendar the events ``stream`` writes to, found through its calendars-stream link."""

        calendar = self.bridge.calendar_for_partition(stream.partition)
        if calendar is None:
            raise IntegrationError("The source's calendar is not mirrored yet.", transient=True)
        return calendar

    def page(
        self,
        stream: Any,
        payloads: Mapping[str, Mapping[str, Any]],
        page_bound: int,
        *,
        after: str | None,
        cursor: dict[str, Any],
    ) -> StreamPage:
        """Return the next bounded page of ``payloads`` in key order after ``after``.

        The cursor carries only the last key while pages remain, and ``cursor``
        commits with the last page. Keys sort, so a series' master precedes its
        instances. Every served record is paged: the driver leaves an unchanged one
        unapplied.
        """

        keys = sorted(key for key in payloads if after is None or key > after)
        batch = keys[: max(1, page_bound)]
        more = len(keys) > len(batch)
        records = self.record_changes(stream, {key: payloads[key] for key in batch})
        return StreamPage(records, {"after": batch[-1], "next": cursor} if more else cursor, exhausted=not more)

    def observe(self, event: ParsedEvent) -> dict[str, Any]:
        """Return the retained source payload of one parsed event, in the form the calendar stores.

        The event owner's stored form (``CalendarEventManager.prepare``) applies
        first, so a stored event projects exactly as it was observed and an
        unchanged source compares unchanged.
        """

        return event_payload(apps.get_model("calendar", "CalendarEvent").objects.prepare(event))

    def record_changes(
        self,
        stream: Any,
        payloads: Mapping[str, Mapping[str, Any] | None],
    ) -> list[RecordChange]:
        """Bind observed payloads (``None`` for a removed key) to their linked rows and projections.

        A payload carrying ``error`` is a remote record the backend could not map;
        the driver quarantines it under its key. An event's remote projection
        compares its attendees on the handles they land on, resolved once for the
        whole batch.
        """

        calendars = stream.key == CALENDARS_STREAM
        states = self._calendar_states(stream, payloads) if calendars else self._local_states(stream, payloads)
        identities: dict[str, str] = {}
        if not calendars:
            addresses = _attendee_addresses(payloads.values())
            handles = apps.get_model("calendar", "CalendarEvent").objects.attendee_handles(addresses)
            identities = {address: attendee_identity(handles.get(address), address) for address in addresses}
        records = []
        for key, payload in payloads.items():
            target, projection = states[key]
            if payload is None:
                records.append(RecordChange(key, {}, "", tombstone=True, target=target, projection=projection))
            elif "error" in payload:
                records.append(
                    RecordChange(
                        key, dict(payload), canonical_json_sha256(payload), failure=SemanticError(payload["error"])
                    )
                )
            else:
                records.append(
                    RecordChange(
                        key,
                        dict(payload),
                        canonical_json_sha256(payload),
                        mapped_payload=(
                            payload["calendar"]
                            if calendars
                            else event_projection(event_from_payload(payload), identities)
                        ),
                        target=target,
                        projection=projection,
                    )
                )
        return records

    def _calendar_states(
        self, stream: Any, keys: Iterable[str]
    ) -> dict[str, tuple[Any | None, dict[str, Any] | None]]:
        """Return each key's linked calendar and the identity this source stated for it, as system work.

        The source's own statement is its side of the comparison: a person's edit
        is a separate statement, never a local change of the source's record.
        """

        keys = set(keys)
        links = {link.external_key: link for link in stream.links.filter(external_key__in=keys)}
        model = apps.get_model("calendar", "Calendar")
        ids = [link.target_object_id for link in links.values() if link.target_object_id]
        rows = {str(row.pk): row for row in model.system_queryset().filter(pk__in=ids)}
        states: dict[str, tuple[Any | None, dict[str, Any] | None]] = {}
        for key in keys:
            link = links.get(key)
            calendar = rows.get(link.target_object_id or "") if link is not None else None
            states[key] = (calendar, self._stated(calendar, link)) if calendar is not None else (None, None)
        return states

    def _stated(self, calendar: Any, link: Any) -> dict[str, Any] | None:
        """Return the identity this source currently asserts on ``calendar`` through ``link``, or ``None``.

        The identity is the fields of ``Calendar.statements``' ``identity`` aspect.
        """

        statements = calendar.statements
        row = (
            statements.current()
            .filter(aspect="identity", link=link, status=statements.model.StatementStatus.ASSERTED)
            .first()
        )
        if row is None:
            return None
        return {name: row.stated_value(name) or "" for name in statements.model.statement_aspects["identity"].fields}

    def _apply_calendar(self, stream: Any, record: RecordChange) -> ApplyResult:
        """Mirror one remote calendar through ``CalendarManager.mirror``; a removed one withdraws its identity.

        A removed calendar keeps its row and events: the source only withdraws the
        identity it stated (``RecordLink.withdraw_statements``), and declares no
        events stream for it any more.
        """

        link = stream.links.get(external_key=record.external_key)
        if record.tombstone:
            calendar, _projection = self._calendar_states(stream, (record.external_key,))[record.external_key]
            link.withdraw_statements()
            return ApplyResult(target=calendar, mapped_payload=None)
        remote = ParsedCalendar(key=record.external_key, **record.source_payload["calendar"])
        calendar = apps.get_model("calendar", "Calendar").objects.mirror(self.bridge, remote, link=link)
        return ApplyResult(target=calendar, mapped_payload=self._stated(calendar, link))

    def _local_states(self, stream: Any, keys: Iterable[str]) -> dict[str, tuple[Any | None, dict[str, Any] | None]]:
        """Return each key's linked event and its current projection, as system work."""

        events = apps.get_model("calendar", "CalendarEvent").objects
        keys = set(keys)
        rows = events.linked(stream, keys)
        parsed = events.project_events(rows.values())
        return {
            key: (rows[key], event_projection(parsed[rows[key].pk])) if key in rows else (None, None) for key in keys
        }

    def prepare_page(self, stream: Any, page: Any) -> None:
        """Lock an events page's work once, in the one lock order: its calendar, then the rows its links name.

        The rows include the series masters the page's instances name, and the
        page's attendee handles resolve once, so the page resolves its calendar,
        every target and every handle once and projects from the rows in hand. A
        source unsubscribed meanwhile refuses the page under that lock
        (:meth:`_require_subscribed`).
        """

        self._page = None
        if stream.key == CALENDARS_STREAM:
            self._require_subscribed()
            return
        keys = {record.external_key for record in page.records}
        keys.update(
            str(record.source_payload["series"])
            for record in page.records
            if not record.tombstone and record.failure is None and record.source_payload.get("series")
        )
        calendar = self.get_calendar(stream).lock_subject()
        self._require_subscribed()
        events = apps.get_model("calendar", "CalendarEvent").objects
        self._page = _EventsPage(
            calendar,
            events.linked(stream, keys, lock=True),
            {link.external_key: link for link in stream.links.filter(external_key__in=keys)},
            events.attendee_handles(_attendee_addresses(record.source_payload for record in page.records)),
        )

    def apply_record(self, stream: Any, record: RecordChange) -> ApplyResult:
        """Write one event through the calendar owners, from the page's locked rows.

        A removed master or single event is cancelled and stays linked; a removed
        instance is released and deleted, which restores the master's own occurrence
        (``CalendarEvent.withdraw``). Each instance key owns its own placement: a
        source that moves a series re-sends its instances under new keys and the old
        ones go through the stream. The model's refusals (an overlap on a ``refuse``
        calendar, an invalid row, a rule beyond its bounds) are ``ValidationError``s
        the driver quarantines per record. A calendars-stream record mirrors its
        calendar instead (:meth:`_apply_calendar`).
        """

        if stream.key == CALENDARS_STREAM:
            return self._apply_calendar(stream, record)
        page = self._page
        assert page is not None  # prepare_page opened it in this page transaction.
        events = apps.get_model("calendar", "CalendarEvent").objects
        key = record.external_key
        link = page.links.get(key) or stream.links.get(external_key=key)
        target = page.rows.get(key)
        if record.tombstone:
            kept = target.withdraw(link) if target is not None else None
            return ApplyResult(target=kept, mapped_payload=self._projection(kept))
        parsed = event_from_payload(record.source_payload)
        series = None
        if parsed.series:
            series = page.rows.get(parsed.series) or events.linked(stream, (parsed.series,)).get(parsed.series)
            if series is None:
                raise SemanticError("series_unavailable", kind=SyncFailureKind.MISSING_DEPENDENCY)
        event = events.ingest(
            page.calendar,
            parsed,
            link=link,
            target=target,
            series=series,
            handles=page.handles,
            created_by_id=self.bridge.owner_id,
        )
        page.rows[key] = event
        return ApplyResult(target=event, mapped_payload=self._projection(event))

    def on_absent(self, stream: Any, links: Sequence[Any]) -> None:
        """Apply integrate's absence verdicts: a tombstoned record is gone from the source.

        An unavailable record keeps its row until the stream's absence threshold
        tombstones it. A tombstoned calendar withdraws the identity its source stated;
        a tombstoned event is withdrawn as a removed record is (:meth:`apply_record`),
        under the calendar's lock first.
        """

        gone = [link for link in links if link.status == LinkStatus.TOMBSTONE]
        if stream.key == CALENDARS_STREAM:
            for link in gone:
                link.withdraw_statements()
            return
        calendar = self.bridge.calendar_for_partition(stream.partition)
        if not gone or calendar is None:
            return
        calendar.lock_subject()
        self._require_subscribed()
        rows = apps.get_model("calendar", "CalendarEvent").objects.linked(
            stream, [link.external_key for link in gone], lock=True
        )
        for link in gone:
            if (event := rows.get(link.external_key)) is not None:
                event.withdraw(link)

    def _require_subscribed(self) -> None:
        """Refuse writing for a source unsubscribed since its sync started.

        Read under the calendar's lock, which the purge takes first, so a page
        already in flight never re-creates what the purge removed.
        """

        rows = type(self.bridge)._base_manager.filter(pk=self.bridge.pk)
        if rows.values_list("lifecycle", flat=True).first() in (None, IntegrationLifecycle.DISCONNECTED):
            raise IntegrationError("This calendar source is unsubscribed.")

    def _projection(self, event: Any | None) -> dict[str, Any] | None:
        """Return the stored projection of the event in hand, or ``None`` once it is gone."""

        if event is None:
            return None
        events = apps.get_model("calendar", "CalendarEvent").objects
        return event_projection(events.project_events((event,))[event.pk])


class ManualCalendarBackend(CalendarBackend):
    """The null object: a source with no backend serves nothing and syncs nothing.

    Keeps ``ANGEE_CALENDAR_SOURCE_BACKEND_CLASSES`` non-empty when no source addon
    is installed (``ImplClassField`` requires a non-empty registry). A local
    calendar has no source at all; this backend is never a subscription.
    """

    key = "manual"
    label = "Manual"

    def discover(self) -> list[ParsedCalendar]:
        """Return no calendars."""

        return []

    def streams(self, *, deadline: float | None = None) -> Iterable[StreamDefinition]:
        """Declare no streams: there is nothing to sync."""

        return ()
