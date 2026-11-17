"""Calendar source mapping: integrate owns the loop and the calendar owns its events.

A source backend discovers the remote calendar it serves and extracts neutral
:class:`ParsedEvent` values keyed by their remote identity. Both sides of the
three-way sync compare the same deterministic projection
(:func:`event_projection`), and every inbound write composes the calendar
managers (``CalendarEventManager.ingest`` and ``withdraw``). Remote identity lives
only in the stream's ``RecordLink`` rows: a master's key is its UID and an
instance's is the UID plus its ``RECURRENCE-ID`` instant (:func:`instance_key`).
The manual backend declares no streams.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, ClassVar

from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models

from angee.base.serialization import canonical_json_sha256
from angee.integrate.http import HttpClientMixin
from angee.integrate.impl import BridgeImpl
from angee.integrate.states import StreamDefinition, SyncFailureKind
from angee.integrate.streams import ApplyResult, RecordChange, SemanticError

ORGANIZER = "organizer"
"""The attendee role naming an event's organizer (``AttendeeRole.ORGANIZER``)."""


@dataclass(frozen=True)
class ParsedCalendar:
    """A remote calendar's display facts; sync progress belongs to its ``SyncStream``.

    ``timezone`` is an IANA name, or blank for the project zone: the calendar
    expands its recurring events in it.
    """

    name: str
    timezone: str = ""
    color: str = ""


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
    calendar's zone. ``series`` is the external key of the recurring master an
    instance overrides and ``original_start`` the instance it replaces; both are
    blank on a master or a single event. Field values use the calendar's
    vocabulary (``EventStatus``, ``EventTransparency``, ``EventVisibility``).
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
    series: str = ""
    original_start: datetime | None = None
    conference_url: str = ""
    attendees: tuple[ParsedAttendee, ...] = ()


def instance_key(uid: str, original_start: datetime) -> str:
    """Return the external key of one instance of the series ``uid``: the UID plus its UTC instant."""

    return f"{uid}#{original_start.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}"


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
    """One events replica stream per calendar a source serves, applied through the calendar managers.

    A concrete backend implements :meth:`probe`, :meth:`discover` and ``extract``;
    it builds its page with :meth:`observe` and :meth:`record_changes`, and this
    base applies each record. ``writable`` declares whether the source takes local
    edits: a read-only backend's calendars refuse them (``Calendar.is_writable``).
    """

    registry_setting = "ANGEE_CALENDAR_SOURCE_BACKEND_CLASSES"

    category = "calendar"
    label = "Calendar"
    icon = "calendar"

    writable: ClassVar[bool] = False
    """Whether calendars of this backend take local edits; a read-only subscription does not."""

    def __init__(self, integration: Any) -> None:
        """Bind to the source; ``_released`` holds the rows a page's records unbind."""

        super().__init__(integration)
        self._released: list[Any] = []

    def probe(self) -> ParsedCalendar:
        """Validate the source before it persists and return the calendar it serves.

        The subscribe or connect verb calls it on the unsaved source, so a source
        that can never sync is never saved. It raises an operator-safe
        ``IntegrationError`` on a bad source.
        """

        raise NotImplementedError("CalendarBackend subclasses must implement probe().")

    def discover(self) -> list[ParsedCalendar]:
        """Return the remote calendar this source serves; a source mirrors one calendar."""

        raise NotImplementedError("CalendarBackend subclasses must implement discover().")

    def streams(self, *, deadline: float | None = None) -> Iterable[StreamDefinition]:
        """Mirror each discovered calendar and declare its events stream."""

        for remote in self.discover():
            yield self.bridge.calendar_stream(remote)

    def calendar(self) -> Any:
        """Return this source's calendar, read as system work."""

        return apps.get_model("calendar", "Calendar").system_queryset().get(source_id=self.bridge.pk)

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
        """Bind observed payloads (``None`` for a removed key) to their linked events and projections.

        A payload carrying ``error`` is a remote record the backend could not map;
        the driver quarantines it under its key.
        """

        states = self._local_states(stream, payloads)
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
                        mapped_payload=payload["event"],
                        target=target,
                        projection=projection,
                    )
                )
        return records

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
        occurrence, which a cancelled override would drop.
        """

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
            self.calendar(),
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
        if not released:
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

    def probe(self) -> ParsedCalendar:
        """Refuse: a manual source has nothing to subscribe to."""

        raise ValidationError("A manual calendar source has nothing to subscribe to.")

    def discover(self) -> list[ParsedCalendar]:
        """Return no calendars."""

        return []
