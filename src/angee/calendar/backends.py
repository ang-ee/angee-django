"""Calendar source mapping: integrate owns the loop and the calendar owns its calendars and events.

A source has two kinds of stream, both record replicas that sync the way the
backend declares (:attr:`CalendarBackend.direction`): pulled in, or published out:

- ``calendars``: one record per remote calendar the backend discovers
  (:class:`ParsedCalendar`), keyed by its remote key. Its ``RecordLink`` names
  the mirrored ``Calendar`` and the source states that calendar's identity
  (``CalendarManager.mirror``), so a person's rename outranks a remote one. The
  stream holds containers: each calendar's link holds that calendar's events stream.
- ``events``, one per remote calendar: its container is the calendar's link and its
  partition that link's key. One record per remote event (:class:`ParsedEvent`). A
  master's key is its UID and an instance's the UID plus its ``RECURRENCE-ID`` instant
  (:func:`instance_key`). Every write composes the calendar owners
  (``CalendarEventManager.ingest`` and ``CalendarEvent.withdraw``).

Both sides of each three-way sync compare one deterministic projection: an event's
(:func:`event_projection`), and for a calendar the identity this source stated, so
a person's own edit is no local change of the source's. Remote identity lives only
in the streams' ``RecordLink`` rows. Extraction pages what the source serves;
integrate's absence sweep finds what it stopped serving (:meth:`CalendarBackend.enumerate_keys`,
:meth:`CalendarBackend.on_absent`). A calendar the source stopped serving retires its
events stream, whose events are then withdrawn as removed records. The manual backend
declares no streams.

A publishing backend (:class:`PublishingCalendarBackend`, ``direction`` ``PUSH``)
mirrors local calendars out instead: ``CalendarSource.publish`` observes the
``calendars`` link of a remote calendar the source writes, targeting an existing local
calendar, and each event of that calendar is pushed through the events stream the link
holds. A push stream never reads its remote, so integrate never sweeps it; the backend
writes conditionally and integrate holds a refused write in the link's outbox.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any, ClassVar

from django.apps import apps
from django.core.exceptions import ValidationError
from icalendar import Component, vRecur

from angee.base.serialization import canonical_json_sha256
from angee.calendar.choices import AttendeeResponse, AttendeeRole, EventStatus
from angee.integrate.errors import IntegrationError
from angee.integrate.http import HttpClientMixin
from angee.integrate.impl import BridgeImpl
from angee.integrate.models import IntegrationLifecycle
from angee.integrate.states import LinkStatus, StreamDefinition, StreamDirection, SyncFailureKind
from angee.integrate.streams import ApplyResult, RecordChange, SemanticError, StreamPage, WriteBackResult
from angee.scheduling.recurrence import MAX_OCCURRENCES, utc_stamp

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

PROJECTED_FIELDS = (*EVENT_FIELDS, "original_start")
"""The columns an event's comparison projection carries, with its attendees (:func:`event_columns`)."""

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


def instance_key(uid: str, original_start: datetime) -> str:
    """Return the external key of one instance of the series ``uid``: its series key plus its UTC instant."""

    return f"{series_key(uid)}#{utc_stamp(original_start)}"


def parse_key(key: str) -> tuple[str | None, datetime | None]:
    """Return what an event key names: its series UID and, for an instance, its original start (aware UTC).

    The inverse of :func:`series_key` and :func:`instance_key`. A digest key names no
    UID (``None``): the UID it was made from cannot be read back.
    """

    series, _hash, instant = key.rpartition("#")
    try:
        moment = datetime.strptime(instant, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC) if series else None
    except ValueError:
        moment = None
    uid = series if moment is not None else key
    return (None if uid.startswith("sha256:") else uid), moment


def organizer_first(
    attendees: Iterable[ParsedAttendee], organizer: str, *, name: str = ""
) -> tuple[ParsedAttendee, ...]:
    """Return a source's attendees with ``organizer`` first, as the event's one ``organizer`` attendee.

    A source gives its organizer's address (and ``name``) beside its raw attendee
    list. The organizer carries the response it is listed with, else accepted, and
    its listed name when ``name`` is blank; its listed entries fold into it. Other
    entries stay as given: ``CalendarEventManager.prepare`` collapses addresses that
    normalize alike (``EventAttendee.collapse``, the stronger role and answer).
    """

    listed = [attendee for attendee in attendees if attendee.address]
    if not organizer:
        return tuple(listed)
    own = [attendee for attendee in listed if attendee.address.lower() == organizer.lower()]
    first = own[0] if own else None
    return (
        ParsedAttendee(
            address=organizer,
            role=AttendeeRole.ORGANIZER,
            response=first.response if first is not None else AttendeeResponse.ACCEPTED,
            display_name=name or (first.display_name if first is not None else ""),
        ),
        *(attendee for attendee in listed if attendee.address.lower() != organizer.lower()),
    )


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


def event_columns(event: Any, fields: Sequence[str] = PROJECTED_FIELDS) -> dict[str, Any]:
    """Return ``fields`` of an event as JSON, moments as UTC ISO-8601 text: the one column projection.

    ``event`` is a :class:`ParsedEvent` or a stored ``CalendarEvent``: both carry
    the columns by name. ``fields`` is a subset of :data:`PROJECTED_FIELDS`; a
    projection that compares fewer columns (a push's) names its own.
    """

    def value(name: str) -> Any:
        found = getattr(event, name)
        return found.astimezone(UTC).isoformat() if name in _MOMENTS and found is not None else found

    return {name: value(name) for name in fields}


def event_projection(event: ParsedEvent, identities: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Return the JSON comparison shape a remote event and a stored row share.

    Attendees compare as a set of ``(identity, role, response)``
    (:func:`attendee_identity`): ``identities`` maps each remote address to its
    identity, and a stored attendee's address already is one. Two addresses on one
    handle collapse into one invitation (``EventAttendee.collapse``), as ingest and
    a merge collapse them. Display names belong to the handle. The series key
    stays out of it: an instance's own key already names its series.
    """

    collapse = apps.get_model("calendar", "EventAttendee").collapse
    attendees: dict[str, tuple[str, str]] = {}
    for attendee in event.attendees:
        identity = identities[attendee.address] if identities is not None else attendee.address
        seen = attendees.get(identity)
        invitation = (attendee.role, attendee.response)
        attendees[identity] = invitation if seen is None else collapse(seen, invitation)
    return {
        **event_columns(event),
        "attendees": sorted([identity, role, response] for identity, (role, response) in attendees.items()),
    }


def event_payload(event: ParsedEvent) -> dict[str, Any]:
    """Return the JSON source payload a stream retains for one remote event.

    Its projected columns, its series key, and its attendees as the source gave
    them: the addresses ingest lands on handles. It names no handle, so its hash
    holds while handles merge.
    """

    return {
        "event": event_columns(event),
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
        **{name: value(name) for name in PROJECTED_FIELDS},
        series=payload["series"],
        attendees=tuple(
            ParsedAttendee(address=address, role=role, response=response, display_name=name)
            for address, role, response, name in payload["attendees"]
        ),
    )


def series_rule(component: Component) -> str:
    """Return the one rule a parsed VEVENT's series keeps: its first ``RRULE`` as RFC 5545 text, else ``""``.

    A stored series has one bare rule (``CalendarEvent.validate_rule``), so a source
    that states several keeps the first; ``RDATE`` and ``EXRULE`` are not mapped.
    """

    rules = component.get("RRULE")
    first = rules[0] if isinstance(rules, list) and rules else rules
    return first.to_ical().decode().strip() if first else ""


def canonical_rule(rule: str) -> str:
    """Return a stored rule's text as :func:`series_rule` reads it back from a source: icalendar's canonical form.

    A backend that writes a rule compares what it wrote with what the source holds
    in this form, so a rule reads back unchanged. Blank stays blank.
    """

    return vRecur.from_ical(rule).to_ical().decode().strip() if rule else ""


def excluded_dates(component: Component) -> list[tuple[date | datetime, str]]:
    """Return each ``EXDATE`` value of a parsed VEVENT with the ``TZID`` it names (``""`` for none).

    A value is a date, a UTC time, a floating time, or a wall-clock time the source
    reads in the named zone itself: the zone icalendar attached is not the source's.
    A VEVENT naming more than :data:`~angee.scheduling.recurrence.MAX_OCCURRENCES`
    of them, the most occurrences the recurrence owner reads of one series, is
    refused (``ValidationError``, code ``too_many_exdates``) before any is mapped.
    """

    found = component.get("EXDATE")
    props = found if isinstance(found, list) else [found] if found is not None else []
    if sum(len(prop.dts) for prop in props) > MAX_OCCURRENCES:
        raise ValidationError(f"The event excludes more than {MAX_OCCURRENCES:,} dates.", code="too_many_exdates")
    return [(value.dt, str(prop.params.get("TZID") or "")) for prop in props for value in prop.dts]


def effective_timezone(zone: str, *, calendar_zone: str) -> str:
    """Return the zone a parsed event keeping ``zone`` keeps on a calendar in ``calendar_zone``.

    ``zone`` is the event's own (``ParsedEvent.timezone``, blank for its
    calendar's) and ``calendar_zone`` its calendar's (blank for the project's).
    ``CalendarEvent.effective_timezone`` decides, asked of an unsaved event on an
    unsaved calendar as :func:`cancelled_instances` asks its series: a backend reads
    a parsed event's times, its ``EXDATE``s and its exceptions' original starts in it.
    """

    # The unsaved rows only lend the event owner's chain; nothing writes them.
    event: Any = apps.get_model("calendar", "CalendarEvent")(
        calendar=apps.get_model("calendar", "Calendar")(timezone=calendar_zone), timezone=zone
    )
    return str(event.effective_timezone)


def cancelled_instances(
    event: ParsedEvent,
    uid: str,
    excluded: Iterable[datetime],
    *,
    calendar_zone: str,
    check_deadline: Callable[[], None],
) -> list[tuple[str, ParsedEvent]]:
    """Validate a parsed master's rule, then return its cancelled instances at ``excluded`` (its ``EXDATE``s).

    ``uid`` is the series' remote identity, keyed by :func:`series_key` and
    :func:`instance_key`. The rule is validated by the event's own owner first
    (``CalendarEvent.validate_rule``: its frequency floor, its ``COUNT`` bound, and
    an occurrence within about 30 years of its start), so an invalid rule raises
    ``ValidationError`` before anything expands it. Each excluded moment is then
    judged on its own (``CalendarEvent.is_instance``, in the zone the stored series
    keeps: ``CalendarEvent.effective_timezone`` on a calendar in ``calendar_zone``),
    so a far ``EXDATE`` reads only its own window; ``check_deadline``, the backend's
    own refusal of a parse past its deadline, runs before each. One that is no
    instance is dropped, and a cancelled instance ends where the event owner's
    arithmetic puts it (``CalendarEvent.occurrence_end``: an all-day instance spans
    local days). Each keeps the master's fields, without its rule and attendees. A
    blank rule returns none.
    """

    if not event.recurrence:
        return []
    # The unsaved rows only lend their owners' zone, rule checks and occurrence arithmetic; nothing writes them.
    series: Any = apps.get_model("calendar", "CalendarEvent")(
        calendar=apps.get_model("calendar", "Calendar")(timezone=calendar_zone),
        start=event.start,
        end=event.end,
        all_day=event.all_day,
        recurrence=event.recurrence,
        timezone=event.timezone,
    )
    zone = series.effective_timezone
    series.validate_rule(zone=zone)
    cancelled = []
    for moment in sorted(set(excluded)):
        check_deadline()
        if not series.is_instance(moment, zone=zone):
            continue
        instance = replace(
            event,
            start=moment,
            end=series.occurrence_end(moment, zone=zone),
            recurrence="",
            series=series_key(uid),
            original_start=moment,
            status=EventStatus.CANCELLED,
            attendees=(),
        )
        cancelled.append((instance_key(uid, moment), instance))
    return cancelled


@dataclass
class _EventsPage:
    """One events page's locked work: its calendar, the rows its links name, and its attendees' handles.

    Rows and links are by external key; handles by the address the source gave.
    All were read before any record applied, so nothing a record's savepoint may
    roll back is ever added to them.
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
    Its calendars are its source's replicas: they take no local edits
    (``Calendar.is_writable`` reads :attr:`direction`). A backend that reads with
    one kind of credential refuses any other (:meth:`credential_blocker`). An
    unsubscribed source's replica goes through :meth:`release`. A publishing
    backend extends :class:`PublishingCalendarBackend` instead.
    """

    registry_setting = "ANGEE_CALENDAR_SOURCE_BACKEND_CLASSES"

    category = "calendar"
    label = "Calendar"
    icon = "calendar"

    direction: ClassVar[StreamDirection] = StreamDirection.PULL
    """Which way this backend's streams sync, the one fact of whether its calendars take local edits.

    ``PULL`` mirrors remote calendars in, as read-only replicas. ``PUSH``
    (:class:`PublishingCalendarBackend`) publishes local calendars out; they stay
    the person's own and take every local edit.
    """

    unsubscribed: ClassVar[str] = "Unsubscribed; its events are being removed."
    """What unsubscribing a source of this backend does, as ``CalendarSource.unsubscribe`` reports it."""

    calendar_delete_refusal: ClassVar[str] = (
        "This calendar mirrors a subscription; unsubscribe to stop it, which archives the calendar."
    )
    """The refusal of deleting one of this backend's calendars on its own (``Calendar.delete_blocker``)."""

    supports_identity_reads: ClassVar[bool] = True
    """Both stream kinds re-read identities (:meth:`read_keys`), so a retried or held record reads its source."""

    events_mapping_version: ClassVar[int] = 1
    """The events stream's mapping version (``StreamDefinition.mapping_version``), declared by the backend that maps.

    Raise it when what the backend maps an event to changes: a pulled stream then
    takes a new baseline, and a push stream compares every event again.
    """

    reconcile_interval: ClassVar[timedelta | None] = None
    """How often each stream of this backend sweeps (``StreamDefinition.reconcile_interval``).

    ``None`` sweeps once per its source's poll interval. A pulled stream's sweep
    finds the records its source stopped serving, so it keeps that interval
    (``CalendarSource._replica``). A push stream's sweep is a push comparing
    every event again (``SyncStream.sweep_due``), which
    :class:`PublishingCalendarBackend` runs hourly: the polls between read only
    the events changed since their push.
    """

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
            remotes = {remote.key: remote.payload() for remote in self._listed_calendars(stream)}
            payloads: Mapping[str, Any] = {key: remotes.get(key) for key in keys}
        else:
            payloads = self.read_events(stream, keys)
        return self.record_changes(stream, {key: payloads.get(key) for key in keys})

    def enumerate_keys(self, stream: Any, *, after: str | None = None) -> list[str]:
        """Return the keys the source serves now, in key order after ``after``: integrate's absence sweep reads them."""

        if stream.key == CALENDARS_STREAM:
            keys: Iterable[str] = (remote.key for remote in self._listed_calendars(stream))
        else:
            keys = self.event_keys(stream)
        return sorted(key for key in set(keys) if after is None or key > after)

    def _listed_calendars(self, stream: Any) -> list[ParsedCalendar]:
        """Return the calendars the source lists for a sweep; an empty list while calendars are linked confirms nothing.

        A listing that comes back empty is not evidence that every calendar went (an
        outage, a revoked grant): the sweep stops, transiently, rather than count them
        all absent.
        """

        remotes = self.discover()
        if not remotes and stream.links.observing().exists():
            raise IntegrationError("The calendar list came back empty; the next sync reads it again.", transient=True)
        return remotes

    def credential_blocker(self, credential: Any) -> str | None:
        """Return a public-safe refusal of a credential this backend cannot use, or ``None`` when it admits it.

        The source's admission rule (``CalendarSource.credential_blocker``):
        :meth:`admit_credential` raises it on every attachment, so no path points a
        source at a credential its backend would send elsewhere, and the connect
        picker shows it beside each account. The base admits any credential.
        """

        del credential
        return None

    def admit_credential(self, credential: Any) -> None:
        """Refuse a credential this backend cannot use, raising :meth:`credential_blocker` on ``credential``.

        ``CalendarSource.admit_credential`` asks on every attachment. A backend
        overrides this only to hold a lock around its rule.
        """

        if (refusal := self.credential_blocker(credential)) is not None:
            raise ValidationError({"credential": refusal})

    def release(self, calendars: Sequence[Any]) -> int:
        """Let an unsubscribed source's replica go; return how many events went.

        ``CalendarSource.purge`` calls it on the backend of the locked, disconnected
        source, with ``calendars`` (the source's) locked and every stream of the
        source fenced. The events streams' links release their targets in bulk
        (``RecordLinkManager.release_targets``), the calendars' events are deleted
        and the calendars archived, with their history and statements. The feed
        credential is detached, and retired through its owner once nothing else
        attaches it (``CredentialManager.retire_unattached``: two subscriptions to
        one feed share it).
        """

        source = self.bridge
        links = apps.get_model("integrate", "RecordLink").objects
        events = apps.get_model("calendar", "CalendarEvent")
        links.release_targets(links.filter(stream__integration_id=source.pk, stream__key=EVENTS_STREAM))
        _deleted, per_model = events.system_queryset().filter(calendar__in=calendars).delete()
        for calendar in calendars:
            if not calendar.is_archived:
                calendar.is_archived = True
                calendar.save(update_fields=["is_archived", "updated_at"])
        credential = source.credential
        if credential is not None:
            source.attach_credential(None)
            apps.get_model("integrate", "Credential").objects.retire_unattached(credential)
        return per_model.get(events._meta.label, 0)

    def streams(self, *, deadline: float | None = None) -> Iterable[StreamDefinition]:
        """Declare the calendars stream, then the events stream each live calendar link holds.

        Local state only: a calendar is known once the ``calendars`` stream applied
        it, and integrate declares again after the wave in which its link appeared
        (``RecordLinkQuerySet.containers_of``).
        """

        yield self.bridge.calendars_stream()
        for link in apps.get_model("integrate", "RecordLink").objects.containers_of(self.bridge, CALENDARS_STREAM):
            yield self.bridge.events_stream(link)

    def extract(self, stream: Any, page_bound: int, *, deadline: float | None = None) -> StreamPage:
        """Extract the calendars stream here; an events stream through the backend's :meth:`extract_events`.

        A source unsubscribed while its sync runs yields an empty last page, so the
        drain ends quietly; its purge removes what is left (:meth:`_require_subscribed`
        guards a page extracted before).
        """

        if not self._subscribed():
            return StreamPage((), dict(stream.cursor or {}))
        if stream.key != CALENDARS_STREAM:
            return self.extract_events(stream, page_bound, deadline=deadline)
        cursor = dict(stream.cursor or {})
        payloads = {remote.key: remote.payload() for remote in self.discover()}
        return self.page(stream, payloads, page_bound, after=cursor.get("after"), cursor={"listed": True})

    def get_calendar(self, stream: Any) -> Any:
        """Return the calendar the events ``stream`` writes to: the target of its container, the calendar's link."""

        calendar = self._container_calendar(stream)
        if calendar is None:
            raise IntegrationError("This events stream's calendar link no longer targets a calendar (released).")
        return calendar

    def _container_calendar(self, stream: Any) -> Any | None:
        """Return the calendar the events ``stream``'s container link targets, as system work, or ``None``."""

        container = stream.container
        if container is None or not container.target_object_id:
            return None
        rows = apps.get_model("calendar", "Calendar").system_queryset()
        return rows.filter(pk=container.target_object_id).first()

    def page(
        self,
        stream: Any,
        payloads: Mapping[str, Mapping[str, Any] | None],
        page_bound: int,
        *,
        after: str | None,
        cursor: dict[str, Any],
        resume: Mapping[str, Any] | None = None,
        last: bool = True,
    ) -> StreamPage:
        """Return the next bounded page of ``payloads`` in key order after ``after``.

        While pages remain the cursor carries the last key (``after``), the cursor
        to commit after them (``next``) and, for a source that reads ``payloads``
        with a request, that request (``from``: ``resume``); ``cursor`` commits
        with the last page, which is the stream's last when ``last``. Keys sort, so
        a series' master precedes its instances. Every served record is paged: the
        driver leaves an unchanged one unapplied.
        """

        keys = sorted(key for key in payloads if after is None or key > after)
        batch = keys[: max(1, page_bound)]
        more = len(keys) > len(batch)
        records = self.record_changes(stream, {key: payloads[key] for key in batch})
        if not more:
            return StreamPage(records, cursor, exhausted=last)
        following = {"after": batch[-1], "next": cursor, **({"from": dict(resume)} if resume is not None else {})}
        return StreamPage(records, following, exhausted=False)

    def observe(self, event: ParsedEvent) -> dict[str, Any]:
        """Return the retained source payload of one parsed event, in the form the calendar stores.

        The event owner's stored form (``CalendarEventManager.prepare``) applies
        first, so a stored event projects exactly as it was observed and an
        unchanged source compares unchanged.
        """

        return event_payload(apps.get_model("calendar", "CalendarEvent").objects.prepare(event))

    def quarantine_series_instances[P: Mapping[str, Any] | None](
        self, stream: Any, payloads: Mapping[str, P]
    ) -> dict[str, P]:
        """Return a listing's ``payloads`` with the linked instances of each series it quarantines, quarantined too.

        A master the backend cannot map (a payload carrying ``error`` under its
        series key) yields none of its ``EXDATE`` instances. A listing without
        their keys reads them as gone, and the absence sweep withdraws them; so each
        instance key a live link of ``stream`` still names and the listing lacks
        takes its master's refusal, until the master maps again. An instance the
        source lists on its own keeps its own payload. The stream's live instance
        keys are read once and matched to their masters here, however many masters
        are refused.
        """

        refused = {
            key: payload
            for key, payload in payloads.items()
            if payload is not None and "error" in payload and parse_key(key)[1] is None
        }
        if not refused:
            return dict(payloads)
        instances = (
            stream.links.filter(external_key__contains="#")
            .exclude(status=LinkStatus.TOMBSTONE)
            .order_by("external_key")
            .values_list("external_key", flat=True)
        )
        held = {
            key: refused[key.rpartition("#")[0]]
            for key in instances
            if key not in payloads and key.rpartition("#")[0] in refused and parse_key(key)[1] is not None
        }
        return {**payloads, **held}

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

        A removed calendar keeps its row: the source withdraws the identity it
        stated (``RecordLink.withdraw_statements``), and integrate retires the events
        stream its link holds, whose events :meth:`on_absent` then withdraws.
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
        if not page.records:
            return
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
            # Read as committed: a master this page applied or refused is never a stale object.
            series = events.linked(stream, (parsed.series,)).get(parsed.series)
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
        return ApplyResult(target=event, mapped_payload=self._projection(event))

    def on_absent(self, stream: Any, links: Sequence[Any]) -> dict[Any, ApplyResult] | None:
        """Apply integrate's absence verdicts: a tombstoned record is gone from the source.

        An unavailable record keeps its row until the stream's absence threshold
        tombstones it. A tombstoned calendar withdraws the identity its source stated;
        a tombstoned event is withdrawn as a removed record is (:meth:`apply_record`),
        under the calendar's lock first. The events of a retired stream (its calendar
        stopped observing) arrive here too. Each withdrawal is returned as its link's
        evidence, so integrate records it as the base and a record that comes back
        applies again.
        """

        gone = [link for link in links if link.status == LinkStatus.TOMBSTONE]
        if stream.key == CALENDARS_STREAM:
            for link in gone:
                link.withdraw_statements()
            # The source no longer states the calendar's identity; its row stays.
            return {link.pk: ApplyResult(mapped_payload=None) for link in gone}
        calendar = self._container_calendar(stream)
        if not gone or calendar is None:
            return None
        calendar.lock_subject()
        self._require_subscribed()
        rows = apps.get_model("calendar", "CalendarEvent").objects.linked(
            stream, [link.external_key for link in gone], lock=True
        )
        withdrawn: dict[Any, ApplyResult] = {}
        for link in gone:
            if (event := rows.get(link.external_key)) is not None:
                kept = event.withdraw(link)
                withdrawn[link.pk] = ApplyResult(target=kept, mapped_payload=self._projection(kept))
        return withdrawn

    def _subscribed(self) -> bool:
        """Whether this source is still subscribed, read fresh from its row."""

        rows = type(self.bridge)._base_manager.filter(pk=self.bridge.pk)
        return rows.values_list("lifecycle", flat=True).first() not in (None, IntegrationLifecycle.DISCONNECTED)

    def _require_subscribed(self) -> None:
        """Refuse a page extracted before its source was unsubscribed.

        Called once the page holds its stream's lock (the driver's) and, for an
        events page, its calendar's: the purge takes every stream of the source first, so a
        page waiting on it reads the disconnect the purge committed and writes
        nothing. Extraction ends a drain quietly (:meth:`extract`); this guards only
        the page already extracted, and fails it rather than revalidate links onto
        rows the purge removed.
        """

        if not self._subscribed():
            raise IntegrationError("This calendar source is unsubscribed.")

    def _projection(self, event: Any | None) -> dict[str, Any] | None:
        """Return the stored projection of the event in hand, or ``None`` once it is gone."""

        if event is None:
            return None
        events = apps.get_model("calendar", "CalendarEvent").objects
        return event_projection(events.project_events((event,))[event.pk])


class PublishingCalendarBackend(CalendarBackend):
    """A backend that publishes local calendars out (``direction`` ``PUSH``) instead of mirroring remote ones.

    ``CalendarSource.publish`` binds a local calendar to a remote one; the calendar
    stays the person's own and takes every local edit. A concrete backend
    implements integrate's ``local_changes``, and :meth:`write_event` and
    :meth:`read_written` instead of the extraction methods: this base runs
    integrate's ``write_back`` and ``read_keys`` through them, after
    :meth:`admit_push`. Its purge unbinds (:meth:`release`).
    """

    direction: ClassVar[StreamDirection] = StreamDirection.PUSH

    reconcile_interval: ClassVar[timedelta | None] = timedelta(hours=1)

    unsubscribed: ClassVar[str] = "Unpublished; its calendars are local again."

    calendar_delete_refusal: ClassVar[str] = "This calendar is published; unpublish it before deleting it."
    """Deleting a published calendar must never remove its remote calendar's events."""

    def write_event(self, link: Any, projection: Any, *, expected_version: str) -> WriteBackResult:
        """Write one event of a published calendar to its remote calendar.

        ``projection`` is ``None`` for an event deleted locally: its remote is removed.
        """

        raise NotImplementedError("A PublishingCalendarBackend must implement write_event().")

    def read_written(self, stream: Any, keys: Sequence[str]) -> list[RecordChange]:
        """Read the remote events ``keys`` name, each with its version."""

        raise NotImplementedError("A PublishingCalendarBackend must implement read_written().")

    def admit_push(self, stream: Any) -> None:
        """Refuse a publishing ``stream``'s remote call, as the stream's failure, before anything is sent.

        Every remote call (a write, an identity read, and the token the first one
        mints) runs behind it: the source is still subscribed (an unsubscribe stops
        the writes of a push already running) and the deployment publishes to the
        stream's remote calendar (``CalendarSource.may_publish_to``). Its refusal
        fails the stream's push once per cycle, never record by record.
        """

        self._require_subscribed()
        if not self.bridge.may_publish_to(stream.partition):
            raise IntegrationError("This deployment does not publish to this calendar.")

    def write_back(self, link: Any, projection: Any, *, expected_version: str) -> WriteBackResult:
        """Write one local change of a published calendar out: :meth:`admit_push`, then :meth:`write_event`."""

        self.admit_push(link.stream)
        return self.write_event(link, projection, expected_version=expected_version)

    def read_keys(self, stream: Any, keys: Sequence[str]) -> list[RecordChange]:
        """Read what was written (:meth:`read_written`), after :meth:`admit_push`."""

        self.admit_push(stream)
        return self.read_written(stream, keys)

    def release(self, calendars: Sequence[Any]) -> int:
        """Unbind an unsubscribed publishing source: nothing remote changes and no event goes; return ``0``.

        Each current stream of the source moves to a new generation first, so a push
        already writing finds its generation superseded and binds nothing it wrote.
        Then every link of the source releases its target, ``calendars`` become local
        again with every event kept, and the credential (the deployment's key) is
        detached, never retired.
        """

        source = self.bridge
        streams = apps.get_model("integrate", "SyncStream").objects
        links = apps.get_model("integrate", "RecordLink").objects
        for stream in streams.current_for_bridge(source):
            streams.bump_generation(stream)
        links.release_targets(links.filter(stream__integration_id=source.pk))
        for calendar in calendars:
            calendar.source = None
            calendar.save(update_fields=["source", "updated_at"])
        if source.credential is not None:
            source.attach_credential(None)
        return 0


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
