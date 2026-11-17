"""Calendar source mapping: integrate owns the loop and the calendar owns its calendars and events.

A source has two kinds of stream, both pull-only record replicas:

- ``calendars``: one record per remote calendar the backend discovers
  (:class:`ParsedCalendar`), keyed by its remote key. Its ``RecordLink`` names
  the mirrored ``Calendar`` and the source states that calendar's identity
  (``CalendarManager.mirror``), so a person's rename outranks a remote one.
- ``events``, one per remote calendar, partitioned by that calendar's key: one
  record per remote event (:class:`ParsedEvent`). A master's key is its UID and an
  instance's the UID plus its ``RECURRENCE-ID`` instant (:func:`instance_key`).
  Every write composes the calendar managers (``CalendarEventManager.ingest`` and
  ``withdraw``).

Both sides of each three-way sync compare one deterministic projection: an event's
(:func:`event_projection`), and for a calendar the identity this source stated, so
a person's own edit is no local change of the source's. Remote identity lives only
in the streams' ``RecordLink`` rows. The manual backend declares no streams.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, ClassVar

from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models

from angee.base.serialization import canonical_json_sha256
from angee.integrate.errors import IntegrationError
from angee.integrate.http import HttpClientMixin
from angee.integrate.impl import BridgeImpl
from angee.integrate.states import LinkStatus, StreamDefinition, SyncFailureKind
from angee.integrate.streams import ApplyResult, RecordChange, SemanticError, StreamPage

ORGANIZER = "organizer"
"""The attendee role naming an event's organizer (``AttendeeRole.ORGANIZER``)."""

CALENDARS_STREAM = "calendars"
"""The stream of a source's remote calendars: one record per calendar, linked to its ``Calendar``."""

EVENTS_STREAM = "events"
"""The stream of one remote calendar's events, partitioned by the calendar's key."""

_IDENTITY = ("name", "color", "timezone")
"""The calendar fields a source states (the ``identity`` aspect of ``Calendar.statements``)."""


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
    """One invited address of a parsed event, with its role and RSVP in the calendar's vocabulary."""

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


def instance_key(uid: str, original_start: datetime) -> str:
    """Return the external key of one instance of the series ``uid``: its series key plus its UTC instant."""

    return f"{series_key(uid)}#{original_start.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}"


def event_projection(event: ParsedEvent) -> dict[str, Any]:
    """Return the JSON comparison shape a remote event and a stored row share.

    Attendees compare as a set of ``(address, role, response)`` on the address the
    ``parties.Handle`` owner normalizes; their display names belong to the handle.
    The series key stays out of it: an instance's own key already names its series.
    """

    def moment(value: datetime | None) -> str | None:
        return value.astimezone(UTC).isoformat() if value is not None else None

    return {
        "title": event.title,
        "description": event.description,
        "location": event.location,
        "start": moment(event.start),
        "end": moment(event.end),
        "all_day": event.all_day,
        "recurrence": event.recurrence,
        "timezone": event.timezone,
        "original_start": moment(event.original_start),
        "status": event.status,
        "transparency": event.transparency,
        "visibility": event.visibility,
        "conference_url": event.conference_url,
        "attendees": sorted([attendee.address, attendee.role, attendee.response] for attendee in event.attendees),
    }


def event_payload(event: ParsedEvent) -> dict[str, Any]:
    """Return the JSON source payload a stream retains for one remote event: its projection and series."""

    return {
        "event": event_projection(event),
        "series": event.series,
        "names": {attendee.address: attendee.display_name for attendee in event.attendees if attendee.display_name},
    }


def event_from_payload(payload: Mapping[str, Any]) -> ParsedEvent:
    """Decode a retained :func:`event_payload` for the calendar's ingest verb."""

    event = payload["event"]
    names = payload.get("names", {})

    def moment(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None

    start, end = moment(event["start"]), moment(event["end"])
    assert start is not None and end is not None  # Every projected event has both.
    return ParsedEvent(
        title=event["title"],
        description=event["description"],
        location=event["location"],
        start=start,
        end=end,
        all_day=event["all_day"],
        recurrence=event["recurrence"],
        timezone=event["timezone"],
        series=payload["series"],
        original_start=moment(event["original_start"]),
        status=event["status"],
        transparency=event["transparency"],
        visibility=event["visibility"],
        conference_url=event["conference_url"],
        attendees=tuple(
            ParsedAttendee(address=address, role=role, response=response, display_name=names.get(address, ""))
            for address, role, response in event["attendees"]
        ),
    )


class CalendarBackend(BridgeImpl, HttpClientMixin):
    """A source's calendars stream and one events stream per calendar, applied through the calendar managers.

    A concrete backend implements :meth:`probe`, :meth:`discover` and
    :meth:`extract_events`; it builds its event pages with :meth:`observe`,
    :meth:`snapshot` and :meth:`page`, and this base extracts the calendars stream
    and applies every record. ``writable`` declares whether the source takes local
    edits: a read-only backend's calendars refuse them (``Calendar.is_writable``).
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
    explicit reliance. A drain that meets an unmirrored calendar fails its
    partition transiently (:meth:`calendar`) and retries on the next cycle.
    """

    def __init__(self, integration: Any) -> None:
        """Bind to the source; ``_released`` holds the rows a page's records unbind."""

        super().__init__(integration)
        self._released: list[Any] = []

    def discover(self) -> list[ParsedCalendar]:
        """Return the remote calendars this source serves, each with its stable key."""

        raise NotImplementedError("CalendarBackend subclasses must implement discover().")

    supports_identity_reads: ClassVar[bool] = True
    """Both stream kinds re-read identities (:meth:`read_keys`), so a retried or held record reads its source."""

    def extract_events(self, stream: Any, page_bound: int, *, deadline: float | None = None) -> StreamPage:
        """Extract one page of the events of the remote calendar ``stream.partition`` names."""

        raise NotImplementedError("CalendarBackend subclasses must implement extract_events().")

    def read_events(self, stream: Any, keys: Sequence[str]) -> dict[str, dict[str, Any] | None]:
        """Read the current payload of each requested event key of ``stream``, ``None`` for one the source lacks."""

        raise NotImplementedError("CalendarBackend subclasses must implement read_events().")

    def read_keys(self, stream: Any, keys: Sequence[str]) -> list[RecordChange]:
        """Re-read the requested identities, each once, a missing one as a tombstone (integrate's identity reads)."""

        if stream.key == CALENDARS_STREAM:
            remotes = {remote.key: remote.payload() for remote in self.discover()}
            payloads: Mapping[str, Any] = {key: remotes.get(key) for key in keys}
        else:
            payloads = self.read_events(stream, keys)
        return self.record_changes(stream, {key: payloads.get(key) for key in keys})

    def streams(self, *, deadline: float | None = None) -> Iterable[StreamDefinition]:
        """Declare the calendars stream, then each discovered calendar's events stream."""

        yield self.bridge.calendars_stream()
        for remote in sorted(self.discover(), key=lambda item: item.key):
            yield self.bridge.calendar_stream(remote)

    def extract(self, stream: Any, page_bound: int, *, deadline: float | None = None) -> StreamPage:
        """Extract the calendars stream here; an events stream through the backend's :meth:`extract_events`."""

        if stream.key != CALENDARS_STREAM:
            return self.extract_events(stream, page_bound, deadline=deadline)
        cursor = dict(stream.cursor or {})
        if "pending" in cursor:
            return self.page(stream, cursor["pending"], page_bound, cursor=cursor["next"])
        payloads = {remote.key: remote.payload() for remote in self.discover()}
        pending = self.snapshot(stream, payloads, baseline=not cursor)
        return self.page(stream, pending, page_bound, cursor={"listed": True})

    def calendar(self, stream: Any) -> Any:
        """Return the calendar the events ``stream`` writes to, found through its calendars-stream link."""

        calendar = self.bridge.calendar_for_partition(stream.partition)
        if calendar is None:
            raise IntegrationError("The source's calendar is not mirrored yet.", transient=True)
        return calendar

    def snapshot(
        self, stream: Any, payloads: Mapping[str, Mapping[str, Any]], *, baseline: bool
    ) -> list[list[Any]]:
        """Compare a full remote snapshot with the stream's links.

        Returns ``[key, payload]`` for each new or changed record (every record on a
        ``baseline``) and ``[key, None]`` for each linked key the snapshot no longer
        carries. Keys sort, so an events series' master precedes its instances.
        """

        links = {
            key: (source_hash, status)
            for key, source_hash, status in stream.links.values_list("external_key", "source_hash", "status")
        }
        pending: list[list[Any]] = []
        for key in sorted(payloads):
            known = links.get(key)
            if (
                baseline
                or known is None
                or known[1] == LinkStatus.TOMBSTONE
                or known[0] != canonical_json_sha256(payloads[key])
            ):
                pending.append([key, payloads[key]])
        pending.extend(
            [key, None] for key, (_hash, status) in sorted(links.items())
            if key not in payloads and status != LinkStatus.TOMBSTONE
        )
        return pending

    def page(self, stream: Any, pending: list[list[Any]], page_bound: int, *, cursor: dict[str, Any]) -> StreamPage:
        """Return one bounded page of ``pending``; the rest rides the cursor, and ``cursor`` commits last."""

        bound = max(1, page_bound)
        batch, remaining = pending[:bound], pending[bound:]
        records = self.record_changes(stream, {key: payload for key, payload in batch})
        return StreamPage(
            records,
            {"next": cursor, "pending": remaining} if remaining else cursor,
            exhausted=not remaining,
        )

    def observe(self, event: ParsedEvent) -> dict[str, Any]:
        """Return the retained source payload of one parsed event, in the form the calendar stores.

        Text is cut to its column, a conference URL that is not a web address is
        dropped, and attendees are deduplicated on the handle owner's normalized
        address (the organizer's row wins), so a stored event projects exactly as it
        was observed and an unchanged feed compares unchanged.
        """

        model = apps.get_model("calendar", "CalendarEvent")
        handles = apps.get_model("parties", "Handle")

        def cut(name: str, value: str) -> str:
            field = model._meta.get_field(name)
            assert isinstance(field, models.Field)
            return value[: field.max_length] if field.max_length else value

        url = event.conference_url.strip()
        try:
            URLValidator(schemes=("http", "https"))(url)
        except ValidationError:
            url = ""
        attendees: dict[str, ParsedAttendee] = {}
        for attendee in event.attendees:
            address = handles.normalize_value(handles.Platform.for_value(attendee.address), attendee.address)
            if not address or (address in attendees and attendee.role != ORGANIZER):
                continue
            attendees[address] = replace(attendee, address=address)
        return event_payload(
            replace(
                event,
                title=cut("title", event.title.strip()),
                location=cut("location", event.location.strip()),
                description=event.description.strip(),
                conference_url=cut("conference_url", url),
                attendees=tuple(attendees.values()),
            )
        )

    def record_changes(
        self,
        stream: Any,
        payloads: Mapping[str, Mapping[str, Any] | None],
    ) -> list[RecordChange]:
        """Bind observed payloads (``None`` for a removed key) to their linked rows and projections.

        A payload carrying ``error`` is a remote record the backend could not map;
        the driver quarantines it under its key.
        """

        calendars = stream.key == CALENDARS_STREAM
        states = self._calendar_states(stream, payloads) if calendars else self._local_states(stream, payloads)
        mapped = "calendar" if calendars else "event"
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
                        mapped_payload=payload[mapped],
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
        """Return the identity this source currently asserts on ``calendar`` through ``link``, or ``None``."""

        statements = calendar.statements
        row = (
            statements.current()
            .filter(aspect="identity", link=link, status=statements.model.StatementStatus.ASSERTED)
            .first()
        )
        if row is None:
            return None
        aspect = statements.model.statement_aspects["identity"]
        return {name: getattr(row, aspect.column(name)) or "" for name in _IDENTITY}

    def _apply_calendar(self, stream: Any, record: RecordChange) -> ApplyResult:
        """Mirror one remote calendar through ``CalendarManager.mirror``; a removed one withdraws its identity.

        A removed calendar keeps its row and events: the source only withdraws the
        identity it stated, and declares no events stream for it any more.
        """

        link = stream.links.get(external_key=record.external_key)
        calendars = apps.get_model("calendar", "Calendar").objects
        if record.tombstone:
            calendar, _projection = self._calendar_states(stream, (record.external_key,))[record.external_key]
            if calendar is not None:
                calendar.statements.withdraw(self.bridge.integration_ptr, link=link)
            return ApplyResult(target=calendar, mapped_payload=None)
        remote = ParsedCalendar(key=record.external_key, **record.source_payload["calendar"])
        calendar = calendars.mirror(self.bridge, remote, link=link)
        return ApplyResult(target=calendar, mapped_payload=self._stated(calendar, link))

    def _local_states(
        self, stream: Any, keys: Iterable[str], *, lock: bool = False
    ) -> dict[str, tuple[Any | None, dict[str, Any] | None]]:
        """Return each key's linked event and its current projection, as system work.

        ``lock`` row-locks the events, for a revalidation inside the page transaction.
        """

        keys = set(keys)
        links = stream.links.filter(external_key__in=keys, target_object_id__isnull=False)
        bound = dict(links.values_list("external_key", "target_object_id"))
        events = apps.get_model("calendar", "CalendarEvent")
        queryset = events.system_queryset(lock=("self",) if lock else None)
        rows = {str(row.pk): row for row in queryset.filter(pk__in=list(bound.values()))}
        parsed = events.objects.parsed(rows.values())
        states: dict[str, tuple[Any | None, dict[str, Any] | None]] = {}
        for key in keys:
            row = rows.get(bound.get(key, ""))
            states[key] = (row, event_projection(parsed[row.pk])) if row is not None else (None, None)
        return states

    def prepare_page(self, stream: Any, page: Any) -> None:
        """Start the page with no released rows."""

        self._released = []

    def apply_record(self, stream: Any, record: RecordChange) -> ApplyResult:
        """Revalidate the linked event under the page transaction, then write it through the calendar managers.

        A removed master or single event is cancelled and stays linked. A removed
        instance, or a row whose place in a series changed, is released: the
        result unbinds its link and :meth:`finish_page` deletes it, since a link
        retains its target. Deleting an instance restores the master's own
        occurrence, which a cancelled override would drop. Each instance key owns its
        own placement: a source that moves a series re-sends its instances under new
        keys and the old ones go through the stream. The model's
        refusals (an overlap on a ``refuse`` calendar, an invalid row) are
        ``ValidationError``s the driver quarantines per record. A calendars-stream
        record mirrors its calendar instead (:meth:`_apply_calendar`).
        """

        if stream.key == CALENDARS_STREAM:
            return self._apply_calendar(stream, record)
        events = apps.get_model("calendar", "CalendarEvent").objects
        target, _projection = self._local_states(stream, (record.external_key,), lock=True)[record.external_key]
        if record.tombstone:
            if target is not None and target.series_id is not None:
                self._released.append(target.pk)
                target = None
            elif target is not None:
                target = events.withdraw(target)
            return ApplyResult(target=target, mapped_payload=self._projection(target))
        parsed = event_from_payload(record.source_payload)
        series = None
        if parsed.series:
            series, _projection = self._local_states(stream, (parsed.series,))[parsed.series]
            if series is None:
                raise SemanticError("series_unavailable", kind=SyncFailureKind.MISSING_DEPENDENCY)
        place = (series.pk, parsed.original_start) if series is not None else (None, None)
        if target is not None and (target.series_id, target.original_start) != place:
            # An event's place in a series is set when it is inserted.
            self._released.append(target.pk)
            target = None
        event = events.ingest(
            self.calendar(stream),
            parsed,
            target=target,
            series=series,
            created_by_id=self.bridge.owner_id,
        )
        return ApplyResult(target=event, mapped_payload=self._projection(event))

    def finish_page(self, stream: Any, page: Any, outcomes: Any) -> None:
        """Delete the rows this page released once the driver has unbound their links.

        A row whose record did not apply is still linked, and stays.
        """

        released, self._released = self._released, []
        if not released or stream.key == CALENDARS_STREAM:
            return
        model = apps.get_model("calendar", "CalendarEvent")
        bound = set(
            stream.links.filter(target_object_id__in=[str(pk) for pk in released]).values_list(
                "target_object_id", flat=True
            )
        )
        for event in model.system_queryset().filter(pk__in=[pk for pk in released if str(pk) not in bound]):
            model.objects.withdraw(event, remove=True)

    def _projection(self, event: Any | None) -> dict[str, Any] | None:
        """Return the stored projection of an applied event, or ``None`` once it is gone."""

        if event is None:
            return None
        events = apps.get_model("calendar", "CalendarEvent").objects
        return event_projection(events.parsed((event,))[event.pk])


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
