"""Calendars, their events and attendees, and the occurrences a calendar shows.

A :class:`Calendar` belongs to one ``parties.Party``: a person, an organization, or
a consumer's child of one. Every :class:`CalendarEvent` belongs to one calendar and
takes its access from it. An event stores **one master row** plus an optional RRULE
(:class:`~angee.scheduling.fields.RecurrenceField`); an exception to a recurring
master is an **override row**, the RFC 5545 RECURRENCE-ID shape: ``series`` names
the master and ``original_start`` the instance it replaces. Attendees are the
``through`` rows of :attr:`CalendarEvent.attendees` to ``parties.Handle``, so the
person behind an address is the handle's resolved party, and the organizer is the
attendee whose ``role`` is ``organizer``.

**Occurrences.** :meth:`CalendarEventQuerySet.occurrences` turns a row set into
the :class:`EventOccurrence` wire shape the calendar renders, composing
:meth:`Recurrence.occurrences` for the moments in each event's expansion zone. An override
replaces the moment at its ``original_start`` and keeps that instance's id, a
cancelled override drops it, and a cancelled event shows nothing. A recurring
master's occurrences are not draggable (drag-rescheduling moves a single row); a
single row is, for an actor who may write it.

**Overlap.** A calendar whose ``overlap_policy`` is ``refuse`` rejects a busy
event any of whose instances meets another busy occurrence on it; free and
cancelled events never conflict. :meth:`CalendarEvent.clean` reports it to a
form, and :meth:`CalendarEvent.save` checks it again under the calendar's row
lock on every save path.

**A series owns its overrides.** When a master's schedule or the zone it follows
changes, :meth:`CalendarEvent.rebase_overrides` carries each override along.

**Sources.** A :class:`CalendarSource` is a connected calendar account or feed,
an ``integrate.Integration`` child. Its ``calendars`` stream mirrors each remote
calendar it serves as a ``Calendar`` whose ``source`` names it and whose identity
the source states; each calendar's ``events`` stream syncs its events. Remote ids,
of calendars and of events, live only in the streams' ``RecordLink`` rows, and
:class:`CalendarEventManager` is the one event write path. A calendar takes local edits
when it has no source or when its source's backend writes
(:attr:`Calendar.is_writable`); a read-only subscription refuses them, and only its
source's sync writes, cancels or removes its events.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any, ClassVar, Self, cast

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models, router, transaction
from django.db.models import F, Q, Value, prefetch_related_objects
from django.db.models.functions import Coalesce, NullIf
from django.utils import timezone
from rebac import permission_expression

from angee.base.fields.implementation import ImplClassField
from angee.base.fields.relations import RetainedGenericRelation
from angee.base.fields.state import StateField
from angee.base.fields.text import ColorField
from angee.base.models.audit import AuditMixin
from angee.base.models.base import AngeeDataModel
from angee.base.models.locks import ImmutableFieldsMixin
from angee.base.models.merge_references import EdgeReference, references
from angee.base.models.ownership import OwnerMixin
from angee.base.models.timestamps import update_fields_with_auto_now
from angee.calendar.backends import (
    CALENDARS_STREAM,
    EVENTS_STREAM,
    CalendarBackend,
    ParsedAttendee,
    ParsedCalendar,
    ParsedEvent,
)
from angee.integrate.models import Bridge, IntegrationCreateMode
from angee.integrate.states import StreamDefinition, StreamDirection, StreamKind
from angee.messaging.models import ThreadedModelMixin
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.provenance.contrib.angee.managers import AngeeHistoryManager
from angee.provenance.contrib.angee.querysets import AngeeHistoryQuerySet
from angee.provenance.statements import Aspect, SourceKind, StatementRecords, StatementsMixin
from angee.scheduling.fields import RecurrenceField, TimeZoneField
from angee.scheduling.recurrence import Recurrence
from angee.scheduling.zones import get_zone_info
from angee.tags.models import TaggedModel


class OverlapPolicy(models.TextChoices):
    """Whether a calendar takes events whose busy time meets another's."""

    ACCEPT = "accept", "Accept overlaps"
    REFUSE = "refuse", "Refuse overlaps"


class EventStatus(models.TextChoices):
    """An event's state (RFC 5545 ``STATUS``); a cancelled event shows no occurrence."""

    CONFIRMED = "confirmed", "Confirmed"
    TENTATIVE = "tentative", "Tentative"
    CANCELLED = "cancelled", "Cancelled"


class EventTransparency(models.TextChoices):
    """Whether an event takes its time (RFC 5545 ``TRANSP``); a free event never conflicts."""

    BUSY = "busy", "Busy"
    FREE = "free", "Free"


class EventVisibility(models.TextChoices):
    """Who reads an event's details, as Google Calendar's event ``visibility`` means it.

    ``default`` follows the calendar's readers, ``public`` also shows the details
    to free/busy viewers, and ``private`` shows them only to the calendar's
    writers; everyone else who sees the calendar's busy times sees the event as
    busy time only. An override created at ``default`` takes its master's.
    """

    DEFAULT = "default", "Default"
    PUBLIC = "public", "Public"
    PRIVATE = "private", "Private"


class AttendeeRole(models.TextChoices):
    """An attendee's part in an event (RFC 5545 ``ROLE``, with the organizer as a role)."""

    ORGANIZER = "organizer", "Organizer"
    REQUIRED = "required", "Required"
    OPTIONAL = "optional", "Optional"


class AttendeeResponse(models.TextChoices):
    """An attendee's RSVP to an event invitation (RFC 5545 ``PARTSTAT`` vocabulary)."""

    NEEDS_ACTION = "needs_action", "Needs Action"
    ACCEPTED = "accepted", "Accepted"
    DECLINED = "declined", "Declined"
    TENTATIVE = "tentative", "Tentative"


READ_ONLY_CALENDAR = "This calendar is a read-only subscription."
"""The refusal of a local edit to an event of a calendar whose source takes no writes."""

SOURCE_CALENDAR_DELETE = "This calendar mirrors a subscription; remove its source to remove it."
"""The refusal of deleting a source's calendar on its own; its source's record link and statements retain it."""

_SOURCE_FIELDS = (
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
"""The event columns a source writes; ``series`` and ``original_start`` follow the override shape."""


@dataclass(frozen=True)
class EventOccurrence:
    """One occurrence the calendar renders: the wire shape ``event_occurrences`` returns.

    ``occurrence_id`` is stable: ``<series_sqid>:<original start, UTC ISO-8601
    basic>`` for every instance of a series, overridden or not, so an id survives
    its instance becoming an override; a plain event is its own ``<event_sqid>``.
    ``event_sqid`` names the row to open (the override, when there is one).
    ``calendar`` is the calendar's public id. A ``redacted`` occurrence is busy time
    an actor may see but not read: it carries no title and opens nothing.
    ``editable`` is whether the actor may drag it: a single row it may write.
    """

    occurrence_id: str
    event_sqid: str
    calendar: str
    title: str
    start: datetime
    end: datetime
    all_day: bool
    editable: bool
    redacted: bool


@dataclass(frozen=True)
class EventMoment:
    """One expanded moment of a row, before it is projected for an actor.

    ``calendar`` is the row's calendar public id, and ``series`` its master's
    (blank for a master or a plain event).
    """

    event: CalendarEvent
    calendar: str
    series: str
    start: datetime
    end: datetime


def _basic(moment: datetime) -> str:
    """A moment as UTC ISO-8601 basic, the occurrence-id suffix."""

    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _nearest_midnight(moment: datetime, tz: Any) -> datetime:
    """The midnight in ``tz`` nearest ``moment``: a dragged date lands on the day the viewer meant."""

    local = moment.astimezone(tz)
    day = local.date() + timedelta(days=1 if local.time() >= time(12) else 0)
    return timezone.make_aware(datetime.combine(day, time.min), tz)


def _same_local_day(moment: datetime, old_tz: Any, new_tz: Any) -> datetime:
    """The midnight in ``new_tz`` on the local date ``moment`` has in ``old_tz``: an all-day bound keeps its date."""

    return timezone.make_aware(datetime.combine(moment.astimezone(old_tz).date(), time.min), new_tz)


def _intervals_meet(left: Sequence[tuple[datetime, datetime]], right: Sequence[tuple[datetime, datetime]]) -> bool:
    """Whether any half-open interval of ``left`` meets one of ``right``; both sorted by start, swept once."""

    i = j = 0
    while i < len(left) and j < len(right):
        (left_start, left_end), (right_start, right_end) = left[i], right[j]
        if left_end <= right_start:
            i += 1
        elif right_end <= left_start:
            j += 1
        else:
            return True
    return False


class CalendarManager[CalendarT: Calendar](AngeeHistoryManager[CalendarT]):
    """Own calendar factories: the calendars a source mirrors through its record links."""

    def mirror(self, source: CalendarSource, remote: ParsedCalendar, *, link: Any) -> CalendarT:
        """Return the calendar ``link`` names, creating it once on the source owner's person; then state its identity.

        ``link`` is the source's ``calendars``-stream ``RecordLink`` for ``remote``:
        the remote calendar's identity. The source states the calendar's name,
        colour and zone (its ``identity`` aspect); a person's own edit outranks the
        source by the aspect's precedence. Runs inside the stream's application
        transaction, as system work.
        """

        rows = self.model.system_queryset()
        calendar = rows.filter(pk=link.target_object_id).first() if link.target_object_id else None
        name = (remote.name or source.display_name)[: self.model._meta.get_field("name").max_length]
        if calendar is None:
            calendar = rows.create(
                source=source,
                party=apps.get_model("parties", "Person").objects.for_user(source.owner),
                name=name,
                timezone=remote.timezone,
                color=remote.color,
                owner=source.owner,
                created_by_id=source.owner_id,
            )
            # The link names the row before its source is admitted to state it.
            apps.get_model("integrate", "RecordLink").objects.bind_target(link, calendar)
        calendar.statements.set(
            aspect="identity",
            values={"name": name, "color": remote.color, "timezone": remote.timezone},
            asserted_by=source.integration_ptr,
            link=link,
        )
        calendar.refresh_from_db()
        return calendar


class Calendar(OwnerMixin, StatementsMixin, HistoryMixin, AngeeDataModel, concrete=True):
    """One party's calendar: the container its events belong to and take access from.

    ``party`` says whose calendar it is. Access is the calendar's own: its
    transferable ``owner`` (the creator by default), its holder (the user of the
    person it belongs to), and the users and groups it is shared with as
    ``reader``, ``editor`` or ``free_busy``; a ``free_busy`` grantee sees the
    calendar's busy time and nothing else. A blank ``timezone`` expands recurrence
    in the project ``TIME_ZONE``; changing it carries the events without a zone of
    their own onto the new wall clock (:meth:`_follow_zone`). History keeps
    ``party``, so reverting a merge of the owning parties restores whose calendar
    it was.
    ``source`` names the :class:`CalendarSource` a subscribed calendar mirrors; a
    local calendar has none.

    A source's calendar is a stated record: the source's ``calendars``-stream
    ``RecordLink`` names it and the source states its ``identity`` (name, colour,
    zone). A person's own edit of those fields outranks the source, so a local
    rename survives a remote one. A local calendar stays an ordinary record: a
    person's edit becomes a stance only once a source states it
    (``person_stances_once_stated``), and a source is admitted by
    ``write_statements`` alone.
    """

    sqid_prefix = "cal_"

    rebac_grantable = {"reader": "share", "editor": "share", "free_busy": "share"}

    person_stances_once_stated = True
    statement_write_permission = "write_statements"
    record_links = RetainedGenericRelation(
        "integrate.RecordLink", content_type_field="target_content_type", object_id_field="target_object_id"
    )
    statements = StatementRecords(
        link="integrate.RecordLink",
        aspects={
            "identity": Aspect(
                fields=("name", "color", "timezone"),
                precedence=cast(
                    tuple[SourceKind, ...],
                    (SourceKind.PERSON, SourceKind.SYSTEM_OF_RECORD, SourceKind.DIRECTORY, SourceKind.IMPORT),
                ),
            ),
        },
        sqid_prefix="cas_",
    )

    party = models.ForeignKey("parties.Party", on_delete=models.PROTECT, related_name="calendars")
    name = models.CharField(max_length=256)
    description = models.TextField(blank=True, default="")
    color = ColorField(max_length=32, blank=True, default="")
    timezone = TimeZoneField()
    overlap_policy = StateField(choices_enum=OverlapPolicy, default=OverlapPolicy.ACCEPT)
    source = models.ForeignKey(
        "calendar.CalendarSource",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="calendars",
    )
    """The connected source this calendar mirrors; empty on a local calendar."""

    objects = CalendarManager()

    class Meta:
        """Django model options for a calendar."""

        abstract = True
        ordering = ("name", "sqid")
        rebac_resource_type = "calendar/calendar"

    def __str__(self) -> str:
        """Return the calendar's name."""

        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist, and carry the events that follow this calendar's zone onto a changed zone.

        The calendar row is locked before its previous zone is read, so two saves
        cannot both rebase from the same zone. When the zone changed, the events
        without a zone of their own follow it in the same transaction
        (:meth:`_follow_zone`).
        """

        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            previous_zone = None if self._state.adding or self.pk is None else self.lock_subject().timezone
            super().save(*args, **kwargs)
            if previous_zone is not None and previous_zone != self.timezone:
                self._follow_zone(previous_zone)

    def _follow_zone(self, previous_zone: str) -> None:
        """Carry the events that keep this calendar's zone from ``previous_zone`` onto the current one.

        An all-day event (a single row, a master or an override) keeps its local
        dates: its ``start`` and ``end`` move to the same midnights on the new wall
        clock. Every series derives its ``series_end`` again, and then each master
        rebases its overrides from its previous start and zone. This is the
        calendar's own derived work, written without the event save path.
        """

        model = cast("type[CalendarEvent]", self._meta.get_field("events").related_model)
        rows = model._base_manager.filter(calendar_id=self.pk, timezone="")
        old_tz, new_tz = get_zone_info(previous_zone), get_zone_info(self.timezone)
        previous_starts: dict[Any, datetime] = {}
        for event in rows.filter(Q(all_day=True) | ~Q(recurrence="")).order_by("pk"):
            previous_starts[event.pk] = event.start
            if event.all_day:
                event.start = _same_local_day(event.start, old_tz, new_tz)
                event.end = _same_local_day(event.end, old_tz, new_tz)
            event.calendar = self
            model._base_manager.filter(pk=event.pk).update(
                start=event.start, end=event.end, series_end=event._derive_series_end()
            )
        masters = rows.filter(series__isnull=True, overrides__isnull=False).distinct().order_by("pk")
        for master in masters:
            master.calendar = self
            previous_start = previous_starts.get(master.pk, master.start)
            master.rebase_overrides(previous_start=previous_start, previous_zone=previous_zone)

    @property
    def is_writable(self) -> bool:
        """Whether this calendar takes local edits: a local calendar always, a source's when its backend writes."""

        source = self.source
        return source is None or CalendarSource.backend_writable(source.backend_class)

    def delete_blocker(self) -> str | None:
        """Refuse deleting a source's calendar on its own: its source's link and statements retain it."""

        if self.source_id is not None:
            return SOURCE_CALENDAR_DELETE
        return cast(Any, super()).delete_blocker()


class CalendarEventQuerySet(AngeeHistoryQuerySet[Any]):
    """Event rows that expand to the occurrences a calendar shows."""

    def moments(self, window_start: datetime, window_end: datetime) -> list[EventMoment]:
        """Return every shown moment of these rows that meets ``[window_start, window_end)``.

        A moment meets the window when it starts before ``window_end`` and ends
        after ``window_start`` (a zero-length moment at ``window_start`` counts). A
        recurring master unrolls its rule in its expansion zone, minus the moments
        its overrides replace; an override is a single row; a cancelled row yields
        nothing, so a cancelled override drops the moment it replaces, and a
        cancelled master takes its moved instances with it. Overrides are read
        through this queryset, so it must hold whole calendars (the actor's scope
        and a calendar filter do). The row set is bounded on both ends: a row
        starting at or after ``window_end`` cannot meet it, and neither can a single
        row ending before ``window_start`` nor a series whose stored
        ``series_end`` is at or before it. An open-ended series is always read.
        """

        rows = list(
            self.annotate(
                occurrence_zone=Coalesce(NullIf(F("timezone"), Value("")), F("calendar__timezone")),
                occurrence_calendar=F("calendar__sqid"),
                occurrence_series=F("series__sqid"),
                occurrence_backend=F("calendar__source__backend_class"),
            )
            .filter(start__lt=window_end)
            .filter(
                Q(recurrence__gt="", series_end__isnull=True)
                | Q(recurrence__gt="", series_end__gt=window_start)
                | Q(end__gt=window_start)
                | Q(start__gte=window_start)
            )
            .exclude(series__status=EventStatus.CANCELLED)
        )
        masters = [row for row in rows if row.recurrence]
        lookback = max((row.end - row.start for row in masters), default=timedelta(0)) + timedelta(days=1)
        replaced: dict[Any, set[datetime]] = defaultdict(set)
        if masters:
            for series_id, original_start in self.filter(
                series__in=[row.pk for row in masters],
                original_start__gte=window_start - lookback,
                original_start__lt=window_end,
            ).values_list("series_id", "original_start"):
                replaced[series_id].add(original_start)

        moments: list[EventMoment] = []
        for row in rows:
            if row.status == EventStatus.CANCELLED:
                continue
            zone = row.occurrence_zone
            if row.recurrence:
                starts = Recurrence(row.recurrence).occurrences(
                    row.start, window_start - (row.end - row.start) - timedelta(days=1), window_end, zone=zone
                )
                starts = [start for start in starts if start not in replaced[row.pk]]
            else:
                starts = [row.start]
            for start in starts:
                end = row.occurrence_end(start, zone=zone)
                if start < window_end and (end > window_start or start >= window_start):
                    moments.append(EventMoment(row, row.occurrence_calendar, row.occurrence_series or "", start, end))
        return moments

    def occurrences(self, window_start: datetime, window_end: datetime) -> list[EventOccurrence]:
        """Return the occurrences this queryset's actor sees in ``[window_start, window_end)``, sorted.

        The rows are those the actor may ``see_busy``. The same row query projects
        whether the actor may ``read`` each row (else it is redacted busy time: no
        title, and nothing at all for a free event) and ``write`` it (a single row it
        may write is draggable). Called on an actor-scoped queryset, the REBAC policy
        is the preflight: an event the actor may not see never enters the set; as
        system work every row reads and writes. A window spans at most
        :attr:`CalendarEvent.occurrence_window_limit`; narrow to calendars by
        filtering this queryset first.
        """

        limit = self.model.occurrence_window_limit
        if window_end - window_start > limit:
            raise ValidationError({"window_end": f"A calendar window spans at most {limit.days} days."})
        actor, unscoped = self.effective_actor()
        if unscoped:
            granted: dict[str, Any] = {"occurrence_read": Value(True), "occurrence_write": Value(True)}
        elif actor is None:
            granted = {"occurrence_read": Value(False), "occurrence_write": Value(False)}
        else:
            granted = {
                "occurrence_read": permission_expression(self.model, "read", actor=actor),
                "occurrence_write": permission_expression(self.model, "write", actor=actor),
            }
        result: list[EventOccurrence] = []
        for moment in self.with_action("see_busy").annotate(**granted).moments(window_start, window_end):
            event = moment.event
            redacted = not event.occurrence_read
            if redacted and event.transparency == EventTransparency.FREE:
                continue
            result.append(
                EventOccurrence(
                    occurrence_id=event.occurrence_id(moment.start, series=moment.series),
                    event_sqid=event.sqid,
                    calendar=moment.calendar,
                    title="" if redacted else event.title,
                    start=moment.start,
                    end=moment.end,
                    all_day=event.all_day,
                    editable=bool(event.occurrence_write)
                    and not event.recurrence
                    and not redacted
                    and CalendarSource.backend_writable(cast(Any, event).occurrence_backend),
                    redacted=redacted,
                )
            )
        result.sort(key=lambda occurrence: (occurrence.start, occurrence.event_sqid))
        return result


_CalendarEventManagerBase = AngeeHistoryManager.from_queryset(CalendarEventQuerySet)


class CalendarEventManager(_CalendarEventManagerBase):
    """Own the source write path of events: ingest, withdrawal and the stored projection a source compares."""

    def parsed(self, events: Iterable[CalendarEvent]) -> dict[Any, ParsedEvent]:
        """Project stored events, with their attendees, into the neutral shape a source compares.

        The series key stays blank: a source names an instance's series by its own key.
        """

        rows = list(events)
        prefetch_related_objects(rows, "event_attendees__handle")
        return {
            event.pk: ParsedEvent(
                **{name: getattr(event, name) for name in _SOURCE_FIELDS},
                original_start=event.original_start,
                attendees=tuple(
                    ParsedAttendee(address=row.handle.normalized_value, role=row.role, response=row.response)
                    for row in event.event_attendees.all()
                ),
            )
            for event in rows
        }

    def ingest(
        self,
        calendar: Calendar,
        parsed: ParsedEvent,
        *,
        target: CalendarEvent | None = None,
        series: CalendarEvent | None = None,
        created_by_id: Any = None,
    ) -> CalendarEvent:
        """Write one event its source observed, with its attendees; a source's sync is the only caller.

        The row validates as every write does (:meth:`CalendarEvent.clean`), except
        that a read-only calendar takes it: its source writes it. An event's place
        in a series (``series``, ``original_start``) is set when it is inserted; the
        caller passes no ``target`` whose place changed. Attendee addresses land on
        handles through ``HandleManager.upsert``; the organizer is the attendee whose
        role is ``organizer``.
        """

        event: Any = target if target is not None else self.model(calendar=calendar, created_by_id=created_by_id)
        for name in _SOURCE_FIELDS:
            setattr(event, name, getattr(parsed, name))
        if target is None:
            event.series = series
            event.original_start = parsed.original_start if series is not None else None
        event._source_write = True
        event.full_clean()
        event.save()
        self._ingest_attendees(event, parsed.attendees, created_by_id=created_by_id)
        return event

    def withdraw(self, event: CalendarEvent, *, remove: bool = False) -> CalendarEvent | None:
        """Apply its source's removal of an event: cancel it and keep it, or with ``remove`` delete it.

        A source cancels a master or a single event, which keeps its chatter and
        tags. It removes an instance once no stream link retains it: the master's
        own occurrence then shows again, which a cancelled override would drop.
        Returns the kept row, or ``None`` once it is deleted.
        """

        event._source_write = True
        if remove:
            event.delete()
            return None
        if event.status != EventStatus.CANCELLED:
            event.status = EventStatus.CANCELLED
            event.save(update_fields=["status", "updated_at"])
        return event

    def _ingest_attendees(
        self, event: CalendarEvent, attendees: Iterable[ParsedAttendee], *, created_by_id: Any
    ) -> None:
        """Make the event's attendee rows the observed set, promoting the organizer last."""

        handles = cast(Any, apps.get_model("parties", "Handle")).objects
        through = cast(Any, apps.get_model("calendar", "EventAttendee"))
        wanted: dict[Any, tuple[str, str]] = {}
        for attendee in attendees:
            handle = handles.upsert(
                platform=handles.model.Platform.for_value(attendee.address),
                value=attendee.address,
                created_by_id=created_by_id,
                display_name=attendee.display_name,
            )
            wanted[handle.pk] = (attendee.role, attendee.response)
        rows = {row.handle_id: row for row in through.system_queryset().filter(event=event)}
        for handle_id, row in rows.items():
            if handle_id not in wanted:
                row._source_write = True
                row.delete()
        # One organizer per event: a former organizer's row changes role or goes first.
        for handle_id, (role, response) in sorted(
            wanted.items(), key=lambda item: item[1][0] == AttendeeRole.ORGANIZER
        ):
            row = rows.get(handle_id) or through(event=event, handle_id=handle_id, created_by_id=created_by_id)
            if row.pk is not None and (row.role, row.response) == (role, response):
                continue
            row.role, row.response = role, response
            row.save()


OVERLAP_MESSAGE = "This calendar is already busy at this time."
"""The overlap refusal: it names no other event, whoever asks."""

_SCHEDULE_FIELDS = frozenset({"start", "end", "all_day", "recurrence", "timezone"})
"""The columns that place a row's instances in time."""

_TIME_FIELDS = _SCHEDULE_FIELDS | {"status", "transparency"}
"""The columns whose change can make a row's busy time meet another's."""


class CalendarEvent(
    ImmutableFieldsMixin, AuditMixin, TaggedModel, ThreadedModelMixin, HistoryMixin, AngeeDataModel, concrete=True
):
    """An event on one calendar, with optional recurrence, overrides and attendees.

    ``start``/``end`` are timezone-aware UTC datetimes; an all-day event stores
    ``start`` as local midnight in its expansion zone and expands by calendar date.
    A master carries the RRULE; an override carries ``series`` and
    ``original_start`` and no rule. Containment is set once: ``calendar``,
    ``series`` and ``original_start`` never change after insert, except when the
    series itself rebases its overrides (:meth:`rebase_overrides`).
    :meth:`reschedule` moves a single row and
    :class:`~angee.provenance.contrib.angee.history.HistoryMixin` records the move in
    the current changeset. :meth:`clean` owns the row's own rules a constraint
    cannot express (the all-day bounds, an override's series); :meth:`save` owns
    what reads other rows (the overlap policy, the rebase), under the calendar's
    row lock, whichever path saves.
    """

    sqid_prefix = "cev_"

    immutable_fields = ("calendar_id", "series_id", "original_start")

    overlap_horizon: ClassVar[timedelta] = timedelta(days=730)
    """How far an open-ended series is checked against a ``refuse`` calendar, from its start."""

    occurrence_window_limit: ClassVar[timedelta] = timedelta(days=400)
    """The longest window :meth:`CalendarEventQuerySet.occurrences` expands."""

    calendar = models.ForeignKey("calendar.Calendar", on_delete=models.CASCADE, related_name="events")
    title = models.CharField(max_length=256)
    description = models.TextField(blank=True, default="")
    location = models.CharField(max_length=256, blank=True, default="")
    start = models.DateTimeField(db_index=True)
    end = models.DateTimeField()
    all_day = models.BooleanField(default=False)
    recurrence = RecurrenceField(finest_frequency="HOURLY")
    timezone = TimeZoneField()
    """The wall clock this event keeps; blank: its calendar's.

    RFC 5545 ``DTSTART;TZID`` and Google's ``start.timeZone`` map here.
    """

    series = models.ForeignKey(
        "calendar.CalendarEvent",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="overrides",
    )
    """The recurring master this row overrides one instance of; empty on a master or a plain event."""

    original_start = models.DateTimeField(null=True, blank=True)
    """The start of the master's instance this override replaces (RFC 5545 ``RECURRENCE-ID``)."""

    series_end = models.DateTimeField(null=True, blank=True, editable=False)
    """Where a bounded series' last instance ends (its rule's ``UNTIL`` or ``COUNT``), derived on save.

    Empty for an open-ended series and for a single row, whose own ``end`` bounds it.
    Reads prune a series that ended before their window.
    """

    status = StateField(choices_enum=EventStatus, default=EventStatus.CONFIRMED)
    transparency = StateField(choices_enum=EventTransparency, default=EventTransparency.BUSY)
    visibility = StateField(choices_enum=EventVisibility, default=EventVisibility.DEFAULT)
    conference_url = models.URLField(max_length=2048, blank=True, default="")
    attendees = models.ManyToManyField(
        "parties.Handle",
        through="calendar.EventAttendee",
        related_name="calendar_events",
        blank=True,
    )

    objects = CalendarEventManager()

    _source_write = False
    """Set on a row by :class:`CalendarEventManager` alone: its source's sync writes or removes it."""

    class Meta:
        """Django model options for a calendar event."""

        abstract = True
        ordering = ("start", "pk")
        rebac_resource_type = "calendar/event"
        indexes = (models.Index(fields=("calendar", "start"), name="%(app_label)s_event_calendar_start"),)
        constraints = (
            models.CheckConstraint(
                condition=Q(end__gte=F("start")),
                name="%(app_label)s_event_end_after_start",
            ),
            models.CheckConstraint(
                condition=Q(series__isnull=True, original_start__isnull=True)
                | Q(series__isnull=False, original_start__isnull=False),
                name="%(app_label)s_event_override_names_its_instance",
            ),
            models.CheckConstraint(
                condition=Q(series__isnull=True) | Q(recurrence=""),
                name="%(app_label)s_event_override_has_no_recurrence",
            ),
            models.UniqueConstraint(
                fields=("series", "original_start"),
                name="%(app_label)s_event_one_override_per_instance",
            ),
        )

    def __str__(self) -> str:
        """Return the event title (or its sqid when untitled)."""

        return self.title or self.sqid

    @property
    def expansion_zone(self) -> str:
        """The zone this event's recurrence, all-day dates and overlap expand on: its own, else its calendar's."""

        return self.timezone or self.calendar.timezone

    def clean(self) -> None:
        """Validate the interval, the all-day bounds and an override's series; this row alone.

        An all-day event's ``start`` and ``end`` are midnights in its expansion zone,
        a whole number of days apart. The overlap policy reads other rows, so
        :meth:`save` checks it, after the write, under the calendar's lock.
        """

        super().clean()
        errors: dict[str, str] = {}
        if self.end is not None and self.start is not None and self.end < self.start:
            errors["end"] = "The event end must not be before its start."
        if self.all_day and self.calendar_id is not None and self.start is not None and self.end is not None:
            errors.update(self._all_day_errors())
        if self.series_id is None:
            if self.original_start is not None:
                errors["original_start"] = "Only an override names the instance it replaces."
        else:
            errors.update(self._override_errors())
        if errors:
            raise ValidationError(errors)
        self._refuse_read_only()

    def _refuse_read_only(self) -> None:
        """Refuse a local write to an event of a read-only calendar; its source's sync writes it.

        Create, update and :meth:`reschedule` all ``full_clean`` through here. The
        calendar is read as system work: the rule holds whoever saves.
        """

        if self._source_write or self.calendar_id is None:
            return
        calendars = type(self)._meta.get_field("calendar").related_model.system_queryset()
        calendar = calendars.select_related("source").filter(pk=self.calendar_id).first()
        if calendar is not None and not calendar.is_writable:
            raise ValidationError(READ_ONLY_CALENDAR)

    def delete_blocker(self) -> str | None:
        """Refuse deleting an event of a read-only calendar; only its source's sync removes it."""

        if not self._source_write and not self.calendar.is_writable:
            return READ_ONLY_CALENDAR
        return cast(Any, super()).delete_blocker()

    def _override_errors(self) -> dict[str, str]:
        """Return why this override does not fit its series, if it does not."""

        series = self.series
        assert series is not None
        if self.recurrence:
            return {"recurrence": "An override is one instance; it carries no recurrence."}
        if series.series_id is not None or not series.recurrence:
            return {"series": "An override belongs to a recurring master event."}
        if series.calendar_id != self.calendar_id:
            return {"calendar": "An override stays on its series' calendar."}
        if self.original_start is None:
            return {"original_start": "An override names the instance it replaces."}
        if not series.is_instance(self.original_start):
            return {"original_start": "The original start is not an instance of the series."}
        return {}

    def _all_day_errors(self) -> dict[str, str]:
        """Return why this all-day event's bounds are not local midnights in its zone."""

        zone = self.expansion_zone
        tz = get_zone_info(zone)
        named = zone or "the project time zone"
        for name in ("start", "end"):
            local = getattr(self, name).astimezone(tz)
            if local.time() != time.min:
                return {name: f"An all-day event {name}s at midnight in its time zone ({named})."}
        if self.end <= self.start:
            return {"end": "An all-day event lasts at least one day."}
        return {}

    def _held_by_record_edges(self) -> bool:
        """Whether a live record edge outside this row (a sync's record link) still names it.

        Such a row belongs to that edge's owner: its sync removes or rewrites it
        through its own stream, so the calendar neither deletes nor detaches it.
        Which edge rows are live and blocking is each edge's own policy
        (:meth:`~angee.base.models.merge_references.Reference.live_rows`).
        """

        return any(
            isinstance(reference, EdgeReference) and reference.live_rows(self).exists()
            for reference in references(type(self))
        )

    def series_defaults(self) -> dict[str, str]:
        """The values an override of this master takes when its insert omits them: visibility and transparency."""

        return {"visibility": self.visibility, "transparency": self.transparency}

    def _derive_series_end(self) -> datetime | None:
        """Return where this series' last instance ends, or ``None`` for an open-ended rule or a single row."""

        if not self.recurrence or self.calendar_id is None:
            return None
        zone = self.expansion_zone
        last = Recurrence(self.recurrence).get_last_occurrence(self.start, zone=zone)
        return self.occurrence_end(last, zone=zone) if last is not None else None

    def is_instance(self, moment: datetime, *, zone: str | None = None) -> bool:
        """Whether ``moment`` is an instance of this master's rule, expanded in ``zone`` (its expansion zone)."""

        if not self.recurrence:
            return False
        zone = self.expansion_zone if zone is None else zone
        instant = timedelta(microseconds=1)
        return moment in Recurrence(self.recurrence).occurrences(self.start, moment, moment + instant, zone=zone)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist under the calendar's lock: write, carry a changed series' overrides along, then check overlap.

        A save that can change this row's time (an insert, or ``update_fields`` naming
        a time column) locks the calendar row first, on any overlap policy, then this
        row, and only then reads what it changes:

        - an override's series is read and validated again under the lock, so it cannot commit
          naming a time that is no longer an instance;
        - the master's previous schedule is read, and after the write its overrides
          rebase onto the new one (:meth:`rebase_overrides`);
        - last, on a ``refuse`` calendar, :meth:`_refuse_overlap` judges the stored
          result; a refusal rolls the whole save back.

        The rule frequency floor and ``series_end`` are enforced and derived here, so
        every save path (a form, a drag, a sync) keeps them.
        """

        update_fields = kwargs.get("update_fields")
        schedule_changes = update_fields is None or bool(_SCHEDULE_FIELDS & set(update_fields))
        takes_time = update_fields is None or bool(_TIME_FIELDS & set(update_fields))
        if schedule_changes:
            self._validate_rule()
            self.series_end = self._derive_series_end()
            if update_fields is not None:
                kwargs["update_fields"] = [*update_fields, "series_end"]
        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            previous = None
            if takes_time and self.calendar_id is not None:
                self.calendar = self.calendar.lock_subject()
                if not self._state.adding and self.pk is not None:
                    super().lock_subject()
                if self.series_id is not None:
                    # A series loaded before the lock (by full_clean) may predate a move it now waits on.
                    self.series = type(self)._base_manager.get(pk=self.series_id)
                    if errors := self._override_errors():
                        raise ValidationError(errors)
                if schedule_changes:
                    previous = self._previous_schedule()
            super().save(*args, **kwargs)
            if previous is not None and previous[:4] != (self.start, self.all_day, self.recurrence, self.timezone):
                self.rebase_overrides(previous_start=previous[0], previous_zone=previous[3] or previous[4])
            if takes_time and self.calendar_id is not None:
                self._refuse_overlap()

    def _validate_rule(self) -> None:
        """Refuse a rule its field refuses (a malformed, unbounded-count or too-frequent rule) on every save."""

        try:
            self._meta.get_field("recurrence").validate(self.recurrence, self)
        except ValidationError as error:
            raise ValidationError({"recurrence": error.messages}) from None

    def _previous_schedule(self) -> tuple[datetime, bool, str, str, str] | None:
        """The stored ``(start, all_day, recurrence, timezone, calendar zone)`` of a master with overrides.

        ``None`` for a new row, an override, or a master without overrides.
        """

        if self._state.adding or self.pk is None or self.series_id is not None:
            return None
        if not type(self)._base_manager.filter(series_id=self.pk).exists():
            return None
        rows = type(self)._base_manager.filter(pk=self.pk)
        return rows.values_list("start", "all_day", "recurrence", "timezone", "calendar__timezone").first()

    def lock_subject(self, members: Mapping[str, Any] | None = None) -> Self:
        """Lock the calendar, then this event: the calendar row serializes its schedule and overlap rule."""

        self.calendar.lock_subject()
        return super().lock_subject(members)

    def rebase_overrides(self, *, previous_start: datetime, previous_zone: str) -> None:
        """Carry this master's overrides onto its changed schedule, in the caller's transaction.

        Each ``original_start`` moves by the master's local wall-clock change, from
        ``previous_start`` in ``previous_zone`` to the current ``start`` in its
        expansion zone. An override that still names an instance keeps it; one that
        no longer does is detached as a standalone event when it carries edits, so
        nothing is lost, and deleted when it is a cancellation. Overrides move in the
        shift's direction, so two never claim the same instance at once. An override a
        sync still links (a protecting record edge names it) is left to that sync,
        neither detached nor deleted: a stranded one suppresses no instance.
        """

        old_tz, zone = get_zone_info(previous_zone), self.expansion_zone
        new_tz = get_zone_info(zone)
        shift = self.start.astimezone(new_tz).replace(tzinfo=None) - previous_start.astimezone(old_tz).replace(
            tzinfo=None
        )
        order = "-original_start" if shift > timedelta(0) else "original_start"
        for override in type(self)._base_manager.filter(series_id=self.pk).order_by(order, "pk"):
            # The check constraint keeps an override's original_start set.
            original = cast(datetime, override.original_start)
            local = original.astimezone(old_tz).replace(tzinfo=None) + shift
            moved = timezone.make_aware(local, new_tz).astimezone(UTC)
            if self.is_instance(moved, zone=zone):
                if moved == override.original_start:
                    continue
                override.original_start = moved
                override.allow_immutable_save("original_start")
                override.save(update_fields=["original_start", "updated_at"])
            elif override._held_by_record_edges():
                # A synced row: its sync tombstones the old instance through its own stream.
                continue
            elif override.status == EventStatus.CANCELLED:
                override.delete()
            else:
                override.series, override.original_start = None, None
                override.allow_immutable_save("series_id", "original_start")
                override.save(update_fields=["series", "original_start", "updated_at"])

    def _refuse_overlap(self) -> None:
        """Refuse this busy event when any of its instances meets another busy occurrence on a ``refuse`` calendar.

        Every instance is checked: a single row's ``[start, end)``, or each instance
        of a series up to its stored ``series_end``, or :attr:`overlap_horizon` from
        its start for an open-ended rule, minus the instances its own overrides
        replace. Both sides are sorted and swept once. This row's stored copy, a
        master's own overrides, and the instance an override replaces are not
        others. :meth:`save` calls it after the write it judges, which the REBAC save
        gates have admitted (a rehearsed save never reaches it), so every row is read
        as system work; the refusal names no other event.
        """

        if self.calendar_id is None or self.start is None or self.end is None or self.end <= self.start:
            return
        if self.status == EventStatus.CANCELLED or self.transparency == EventTransparency.FREE:
            return
        if self.calendar.overlap_policy != OverlapPolicy.REFUSE:
            return
        if self.series_id is not None and self.series is not None and self.series.status == EventStatus.CANCELLED:
            return
        mine = self._own_instances()
        if not mine:
            return
        rows = type(self).system_queryset().filter(calendar_id=self.calendar_id)
        theirs = sorted(
            (moment.start, moment.end)
            for moment in rows.moments(mine[0][0], max(end for _start, end in mine))
            if moment.event.transparency == EventTransparency.BUSY and not self._is_own(moment)
        )
        if _intervals_meet(mine, theirs):
            raise ValidationError({"start": OVERLAP_MESSAGE})

    def _own_instances(self) -> list[tuple[datetime, datetime]]:
        """This row's instances, sorted: itself, or its series to ``series_end`` (open-ended: the overlap horizon)."""

        if not self.recurrence:
            return [(self.start, self.end)]
        zone = self.expansion_zone
        stop = self.series_end or self.start + self.overlap_horizon
        starts = Recurrence(self.recurrence).occurrences(self.start, self.start, stop, zone=zone)
        replaced: set[datetime] = set()
        if self.pk is not None:
            stored = type(self)._base_manager.filter(series_id=self.pk)
            replaced = {start for start in stored.values_list("original_start", flat=True) if start is not None}
        return [(start, self.occurrence_end(start, zone=zone)) for start in starts if start not in replaced]

    def _is_own(self, moment: EventMoment) -> bool:
        """Whether ``moment`` is this row's own: its stored copy, its overrides, or the instance it replaces."""

        other = moment.event
        if self.pk is not None and (other.pk == self.pk or other.series_id == self.pk):
            return True
        return other.pk == self.series_id and moment.start == self.original_start

    def occurrence_end(self, start: datetime, *, zone: str = "") -> datetime:
        """Return the end of this row's occurrence starting at ``start``, expanded in ``zone``.

        A **timed** event keeps its absolute duration (``end - start``), so a
        60-minute meeting stays 60 minutes across a DST shift. An **all-day** event
        spans a whole number of **calendar days** in the zone: the row's local
        day-count is added to the occurrence's local start date and converted back
        to UTC, so a one-day all-day event ends the next local midnight, not one hour
        off after a spring-forward. A blank zone is the project ``TIME_ZONE``.
        """

        if not self.all_day:
            return start + (self.end - self.start)
        tz = get_zone_info(zone)
        day_span = (self.end.astimezone(tz).date() - self.start.astimezone(tz).date()).days
        end_local_date = start.astimezone(tz).date() + timedelta(days=day_span)
        end_local = datetime.combine(end_local_date, time.min)
        return timezone.make_aware(end_local, tz).astimezone(UTC)

    def occurrence_id(self, start: datetime, *, series: str = "") -> str:
        """Return the stable id of this row's occurrence at ``start`` (see :class:`EventOccurrence`).

        ``series`` is an override's master public id: the override keeps the id of
        the instance it replaces.
        """

        if self.series_id is not None and self.original_start is not None:
            if not series:
                raise ValueError("An override's occurrence id needs its master's public id.")
            return f"{series}:{_basic(self.original_start)}"
        if not self.recurrence:
            return self.sqid
        return f"{self.sqid}:{_basic(start)}"

    def reschedule(self, start: datetime, end: datetime) -> Self:
        """Move this single row to new ``start``/``end``, validate, and persist with history.

        A recurring master refuses: its occurrences are dragged one instance at a
        time through an override, and the series itself is edited through the
        event form. An all-day event lands on the nearest midnights in its expansion
        zone, since a drag carries the viewer's own midnight. The moved
        fields are validated before the write (:meth:`clean`: the interval and the
        series) and the overlap policy by the save, so an invalid move surfaces as a
        ``ValidationError``, never an ``IntegrityError``. The ordinary save joins the
        caller's changeset, so provenance owns the resulting field delta and reason.
        """

        if self.recurrence:
            raise ValidationError(
                {"start": "A recurring event's occurrences cannot be dragged; edit the series instead."}
            )
        if self.all_day:
            tz = get_zone_info(self.expansion_zone)
            start, end = _nearest_midnight(start, tz), _nearest_midnight(end, tz)
            if end <= start:
                next_day = start.astimezone(tz).date() + timedelta(days=1)
                end = timezone.make_aware(datetime.combine(next_day, time.min), tz)
        self.start = start
        self.end = end
        self.full_clean(
            exclude=[field.name for field in self._meta.fields if field.name not in ("start", "end")],
            validate_unique=False,
        )
        self.save(update_fields=["start", "end", "updated_at"])
        return self


class EventAttendee(ImmutableFieldsMixin, AuditMixin, HistoryMixin, AngeeDataModel, concrete=True):
    """One handle invited to an event: the ``through`` row of :attr:`CalendarEvent.attendees`.

    The person behind the address is the handle's resolved party, so syncing an
    invitation never invents a party per address. ``role`` names the organizer
    (at most one per event) and ``response`` the RSVP, ``needs_action`` until
    answered. Access rides the event: ``calendar/event_attendee`` reads and writes
    through ``event->read`` and ``event->write``, and an event's readers read its
    attendees' handles. The ``event`` is set once. ``(event, handle)`` is unique, so
    demo rows reload idempotently on the xref ledger.
    """

    sqid_prefix = "eat_"

    immutable_fields = ("event_id",)

    event = models.ForeignKey("calendar.CalendarEvent", on_delete=models.CASCADE, related_name="event_attendees")
    handle = models.ForeignKey("parties.Handle", on_delete=models.PROTECT, related_name="event_attendees")
    role = StateField(choices_enum=AttendeeRole, default=AttendeeRole.REQUIRED)
    response = StateField(choices_enum=AttendeeResponse, default=AttendeeResponse.NEEDS_ACTION)

    class Meta:
        """Django model options for an event attendee."""

        abstract = True
        ordering = ("event_id", "pk")
        rebac_resource_type = "calendar/event_attendee"
        constraints = (
            models.UniqueConstraint(fields=("event", "handle"), name="%(app_label)s_attendee_one_per_handle"),
            models.UniqueConstraint(
                fields=("event",),
                condition=Q(role="organizer"),
                name="%(app_label)s_attendee_one_organizer",
            ),
        )

    merge_identity = (("event", "handle"),)
    """A handle is invited to an event once: merging two handles folds their invitations into one."""

    def __str__(self) -> str:
        """Return a readable label for Django displays."""

        return f"{self.event_id}:{self.handle_id}"

    def merge_collapse(self, duplicate: Any) -> bool:
        """Keep one invitation covering both: the organizer role, else the stronger role, and the stronger answer.

        An answer outranks none, and a definite answer a tentative one, with
        acceptance first: ``accepted`` > ``declined`` > ``tentative`` > ``needs_action``.
        The duplicate gives up the organizer role first, since one event has one
        organizer and the duplicate goes only after this row is saved.
        """

        role = min((self.role, duplicate.role), key=_ROLE_RANK.index)
        response = max((self.response, duplicate.response), key=_RESPONSE_RANK.index)
        if role == AttendeeRole.ORGANIZER and duplicate.role == AttendeeRole.ORGANIZER:
            duplicate.role = AttendeeRole.REQUIRED
            duplicate.save(update_fields=update_fields_with_auto_now(duplicate, {"role"}))
        dirty = {name for name, value in (("role", role), ("response", response)) if getattr(self, name) != value}
        self.role, self.response = role, response
        if dirty:
            self.save(update_fields=update_fields_with_auto_now(self, dirty))
        return True

    _source_write = False
    """Set on a row by :class:`CalendarEventManager` alone: its source's sync removes it."""

    def clean(self) -> None:
        """Refuse a local attendee write on an event of a read-only calendar."""

        super().clean()
        if self.event_id is not None and not self.event.calendar.is_writable:
            raise ValidationError(READ_ONLY_CALENDAR)

    def delete_blocker(self) -> str | None:
        """Refuse deleting an attendee of an event on a read-only calendar; only its source's sync removes it."""

        if not self._source_write and not self.event.calendar.is_writable:
            return READ_ONLY_CALENDAR
        return cast(Any, super()).delete_blocker()


_ROLE_RANK = (AttendeeRole.ORGANIZER, AttendeeRole.REQUIRED, AttendeeRole.OPTIONAL)
"""Attendee roles, strongest first."""

_RESPONSE_RANK = (
    AttendeeResponse.NEEDS_ACTION,
    AttendeeResponse.TENTATIVE,
    AttendeeResponse.DECLINED,
    AttendeeResponse.ACCEPTED,
)
"""Attendee answers, weakest first."""


class CalendarSource(Bridge, inherits="integrate.Integration"):
    """A connected calendar account or feed that mirrors its remote calendar and syncs its events.

    An ``integrate.Integration`` child (its owner, credential and status come from
    the connection substrate) and a ``Bridge`` (the scheduler and the eager
    ``syncIntegration`` mutation drive it). ``backend_class`` selects the protocol
    (``ics``, contributed by ``calendar_integrate_ics``) and ``config`` carries what
    it needs, such as the feed URL.

    The source mirrors each remote calendar it serves through its ``calendars``
    stream (:meth:`calendars_stream`): one record per remote calendar, whose
    ``RecordLink`` names its :class:`Calendar` on the owner's person and whose
    source states that calendar's identity. Each remote calendar's events sync
    through an ``events`` stream partitioned by its key (:meth:`calendar_stream`);
    the calendar managers own every event write. Whether a calendar takes local
    edits is its backend's ``writable`` declaration (:meth:`backend_writable`).
    """

    integration_create_mode: ClassVar[IntegrationCreateMode | None] = IntegrationCreateMode.CONNECT

    @property
    def source_kind(self) -> SourceKind:
        """A calendar source states its calendars as a directory: below a person's own edits."""

        return cast(SourceKind, SourceKind.DIRECTORY)

    backend_class = ImplClassField(CalendarBackend, create_only=True)
    """Registry key of the calendar backend bound to this source."""

    class Meta:
        """Django model options for the calendar source child model."""

        abstract = True
        ordering = ("-updated_at",)
        rebac_resource_type = "calendar/calendar_source"

    @property
    def backend(self) -> CalendarBackend:
        """Return this source's selected backend, bound to this row."""

        backend_class = self.get_backend_class_impl()
        return backend_class(self)

    @classmethod
    def backend_writable(cls, key: str | None) -> bool:
        """Whether a calendar whose source has backend ``key`` takes local edits; no source (``None``) does."""

        return key is None or cls._meta.get_field("backend_class").resolve_class(key).writable

    def calendar_for_partition(self, partition: str) -> Any | None:
        """Return the calendar an ``events`` stream partition writes to, or ``None`` before it is mirrored.

        The one place that resolves a partition to its container: the
        ``calendars``-stream ``RecordLink`` whose external key is the partition
        names the calendar. Integrate has no partition-to-container seam yet; this
        lookup is the interim one, to be replaced by it.
        """

        return (
            apps.get_model("calendar", "Calendar")
            .system_queryset()
            .filter(
                record_links__stream__integration_id=self.pk,
                record_links__stream__key=CALENDARS_STREAM,
                record_links__external_key=partition,
            )
            .first()
        )

    def calendars_stream(self) -> StreamDefinition:
        """Declare the stream of the remote calendars this source serves: one record per calendar."""

        return StreamDefinition(CALENDARS_STREAM, kind=StreamKind.RECORD_REPLICA, direction=StreamDirection.PULL)

    def calendar_stream(self, remote: ParsedCalendar) -> StreamDefinition:
        """Declare the events stream of one remote calendar, partitioned by its key.

        A pull-only record replica keyed by remote event identity. A backend that
        takes writes makes its calendar writable; it does not change the stream.
        """

        return StreamDefinition(
            EVENTS_STREAM, partition=remote.key, kind=StreamKind.RECORD_REPLICA, direction=StreamDirection.PULL
        )
