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
:meth:`Recurrence.occurrences` for the moments in the calendar's zone. An override
replaces the moment at its ``original_start``, a cancelled override drops it, and
a cancelled event shows nothing. A recurring master's occurrences are not
editable (drag-rescheduling applies to a single row); an override's occurrence is
its own row, so it is.

**Overlap.** A calendar whose ``overlap_policy`` is ``refuse`` rejects a busy
event whose ``[start, end)`` meets another busy occurrence on it; free and
cancelled events never conflict. :meth:`CalendarEvent.clean` reports it to a
form, and :meth:`CalendarEvent.save` checks it again under the calendar's row
lock on every save path.

**A series owns its overrides.** When a master's schedule or its calendar's zone
changes, :meth:`CalendarEvent.rebase_overrides` carries each override along.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any, Self, cast

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import models, router, transaction
from django.db.models import F, Q
from django.utils import timezone

from angee.base.fields.state import StateField
from angee.base.fields.text import ColorField
from angee.base.models.audit import AuditMixin
from angee.base.models.base import AngeeDataModel
from angee.base.models.locks import ImmutableFieldsMixin
from angee.base.models.ownership import OwnerMixin
from angee.messaging.models import ThreadedModelMixin
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.provenance.contrib.angee.managers import AngeeHistoryManager
from angee.provenance.contrib.angee.querysets import AngeeHistoryQuerySet
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
    """Who reads an event's details (RFC 5545 ``CLASS``).

    ``default`` follows the calendar's readers, ``public`` also shows the details
    to free/busy viewers, and ``private`` shows them only to the calendar's
    writers; everyone else who sees the calendar's busy times sees the event as
    busy time only.
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


@dataclass(frozen=True)
class EventOccurrence:
    """One occurrence the calendar renders: the wire shape ``event_occurrences`` returns.

    ``occurrence_id`` is stable: ``<event_sqid>:<UTC ISO-8601 basic start>`` for a
    recurring master's instance, and the row's own ``<event_sqid>`` for a single
    row (a plain event or an override). ``calendar`` is the calendar's public id.
    A ``redacted`` occurrence is busy time an actor may see but not read: it carries
    no title and opens nothing. ``editable`` gates drag-rescheduling.
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
    """One expanded moment of a row, before it is projected for an actor."""

    event: CalendarEvent
    calendar: str
    start: datetime
    end: datetime


class Calendar(OwnerMixin, HistoryMixin, AngeeDataModel, concrete=True):
    """One party's calendar: the container its events belong to and take access from.

    ``party`` says whose calendar it is. Access is the calendar's own: its
    transferable ``owner`` (the creator by default), its holder (the user of the
    person it belongs to), and the users and groups it is shared with as
    ``reader``, ``editor`` or ``free_busy``; a ``free_busy`` grantee sees the
    calendar's busy time and nothing else. A blank ``timezone`` expands recurrence
    in the project ``TIME_ZONE``; changing it carries every series' overrides onto
    the new wall clock (:meth:`CalendarEvent.rebase_overrides`). History keeps
    ``party``, so reverting a merge of the owning parties restores whose calendar
    it was.
    """

    sqid_prefix = "cal_"

    rebac_grantable = {"reader": "share", "editor": "share", "free_busy": "share"}

    party = models.ForeignKey("parties.Party", on_delete=models.PROTECT, related_name="calendars")
    name = models.CharField(max_length=256)
    description = models.TextField(blank=True, default="")
    color = ColorField(max_length=32, blank=True, default="")
    timezone = TimeZoneField()
    overlap_policy = StateField(choices_enum=OverlapPolicy, default=OverlapPolicy.ACCEPT)

    class Meta:
        """Django model options for a calendar."""

        abstract = True
        ordering = ("name", "sqid")
        rebac_resource_type = "calendar/calendar"

    def __str__(self) -> str:
        """Return the calendar's name."""

        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist, then rebase every series' overrides when the time zone changed.

        The zone is the wall clock recurrence expands on, so an override's
        ``original_start`` follows it in the same transaction.
        """

        previous_zone: str | None = None
        if not self._state.adding and self.pk is not None:
            previous_zone = type(self)._base_manager.filter(pk=self.pk).values_list("timezone", flat=True).first()
        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            super().save(*args, **kwargs)
            if previous_zone is None or previous_zone == self.timezone:
                return
            events = cast("type[CalendarEvent]", self._meta.get_field("events").related_model)._base_manager
            masters = events.filter(calendar_id=self.pk, series__isnull=True, overrides__isnull=False).distinct()
            for master in masters.order_by("pk"):
                master.rebase_overrides(previous_start=master.start, previous_zone=previous_zone)


class CalendarEventQuerySet(AngeeHistoryQuerySet[Any]):
    """Event rows that expand to the occurrences a calendar shows."""

    def moments(self, window_start: datetime, window_end: datetime) -> list[EventMoment]:
        """Return every shown moment of these rows that meets ``[window_start, window_end)``.

        A moment meets the window when it starts before ``window_end`` and ends
        after ``window_start`` (a zero-length moment at ``window_start`` counts). A
        recurring master unrolls its rule in its calendar's zone, minus the moments
        its overrides replace; an override is a single row; a cancelled row yields
        nothing, so a cancelled override drops the moment it replaces, and a
        cancelled master takes its moved instances with it. Overrides are read
        through this queryset, so it must hold whole calendars (the actor's scope
        and a calendar filter do). The row set is bounded on both ends: a row
        starting at or after ``window_end`` cannot meet it, and neither can a single
        row ending before ``window_start``. A recurring master is kept whatever its
        ``end``, which bounds only its first instance.
        """

        rows = list(
            self.annotate(occurrence_zone=F("calendar__timezone"), occurrence_calendar=F("calendar__sqid"))
            .filter(start__lt=window_end)
            .filter(Q(recurrence__gt="") | Q(end__gt=window_start) | Q(start__gte=window_start))
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
                    moments.append(EventMoment(row, row.occurrence_calendar, start, end))
        return moments

    def occurrences(
        self,
        window_start: datetime,
        window_end: datetime,
        *,
        calendars: Collection[str] | None = None,
    ) -> list[EventOccurrence]:
        """Return the occurrences this queryset's actor sees in ``[window_start, window_end)``, sorted.

        The rows are those the actor may ``see_busy``; the ones it may also ``read``
        project in full, the others as redacted busy time (no title, not editable,
        and nothing at all for a free event). ``calendars`` narrows to calendars by
        public id. Called on an actor-scoped queryset, the REBAC policy is the
        preflight: an event the actor may not see never enters the set.
        """

        visible = self.with_action("see_busy")
        if calendars is not None:
            visible = visible.filter(calendar__sqid__in=list(calendars))
        moments = visible.moments(window_start, window_end)
        ids = {moment.event.pk for moment in moments}
        readable = set(self.with_action("read").filter(pk__in=ids).values_list("pk", flat=True)) if ids else set()
        result: list[EventOccurrence] = []
        for moment in moments:
            event = moment.event
            redacted = event.pk not in readable
            if redacted and event.transparency == EventTransparency.FREE:
                continue
            result.append(
                EventOccurrence(
                    occurrence_id=event.occurrence_id(moment.start),
                    event_sqid=event.sqid,
                    calendar=moment.calendar,
                    title="" if redacted else event.title,
                    start=moment.start,
                    end=moment.end,
                    all_day=event.all_day,
                    editable=not (event.recurrence or redacted),
                    redacted=redacted,
                )
            )
        result.sort(key=lambda occurrence: (occurrence.start, occurrence.event_sqid))
        return result


CalendarEventManager = AngeeHistoryManager.from_queryset(CalendarEventQuerySet)


OVERLAP_MESSAGE = "This calendar is already busy at this time."
"""The overlap refusal: it names no other event, whoever asks."""

_TIME_FIELDS = frozenset({"start", "end", "all_day", "recurrence", "status", "transparency"})
"""The columns whose change can make a row's busy time meet another's."""


class CalendarEvent(
    ImmutableFieldsMixin, AuditMixin, TaggedModel, ThreadedModelMixin, HistoryMixin, AngeeDataModel, concrete=True
):
    """An event on one calendar, with optional recurrence, overrides and attendees.

    ``start``/``end`` are timezone-aware UTC datetimes; an all-day event stores
    ``start`` as local midnight in its calendar's zone and expands by calendar date.
    A master carries the RRULE; an override carries ``series`` and
    ``original_start`` and no rule. Containment is set once: ``calendar``,
    ``series`` and ``original_start`` never change after insert, except when the
    series itself rebases its overrides (:meth:`rebase_overrides`).
    :meth:`reschedule` moves a single row and
    :class:`~angee.provenance.contrib.angee.history.HistoryMixin` records the move in
    the current changeset. :meth:`clean` owns the rules a constraint cannot express
    (an override's series, the calendar's overlap policy), and :meth:`save` checks
    the overlap policy again under the calendar's row lock, whichever path saves.
    """

    sqid_prefix = "cev_"

    immutable_fields = ("calendar_id", "series_id", "original_start")

    calendar = models.ForeignKey("calendar.Calendar", on_delete=models.CASCADE, related_name="events")
    title = models.CharField(max_length=256)
    description = models.TextField(blank=True, default="")
    location = models.CharField(max_length=256, blank=True, default="")
    start = models.DateTimeField(db_index=True)
    end = models.DateTimeField()
    all_day = models.BooleanField(default=False)
    recurrence = RecurrenceField()
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

    class Meta:
        """Django model options for a calendar event."""

        abstract = True
        ordering = ("start", "pk")
        rebac_resource_type = "calendar/event"
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

    def clean(self) -> None:
        """Validate the interval, an override's series, and the calendar's overlap policy."""

        super().clean()
        errors: dict[str, str] = {}
        if self.end is not None and self.start is not None and self.end < self.start:
            errors["end"] = "The event end must not be before its start."
        if self.series_id is None:
            if self.original_start is not None:
                errors["original_start"] = "Only an override names the instance it replaces."
        else:
            errors.update(self._override_errors())
        if errors:
            raise ValidationError(errors)
        self._refuse_overlap()

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

    def is_instance(self, moment: datetime, *, zone: str | None = None) -> bool:
        """Whether ``moment`` is an instance of this master's rule, expanded in ``zone`` (its calendar's)."""

        if not self.recurrence:
            return False
        zone = self.calendar.timezone if zone is None else zone
        instant = timedelta(microseconds=1)
        return moment in Recurrence(self.recurrence).occurrences(self.start, moment, moment + instant, zone=zone)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist under the calendar's overlap rule, and carry a changed series' overrides along.

        On a ``refuse`` calendar a save that can change this row's busy time locks
        the calendar row first and checks the overlap policy again under that lock,
        so two concurrent saves cannot both take the same time, and the rule holds on
        every save path (a form, a drag, a sync). A saved master whose ``start``,
        ``all_day`` or ``recurrence`` changed rebases its overrides in the same
        transaction (:meth:`rebase_overrides`).
        """

        update_fields = kwargs.get("update_fields")
        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            previous = self._previous_schedule()
            takes_time = update_fields is None or bool(_TIME_FIELDS & set(update_fields))
            if takes_time and self.calendar_id is not None and self.calendar.overlap_policy == OverlapPolicy.REFUSE:
                self.calendar = self.calendar.lock_subject()
                self._refuse_overlap()
            super().save(*args, **kwargs)
            if previous is not None and previous != (self.start, self.all_day, self.recurrence):
                self.rebase_overrides(previous_start=previous[0], previous_zone=self.calendar.timezone)

    def _previous_schedule(self) -> tuple[datetime, bool, str] | None:
        """The stored ``(start, all_day, recurrence)`` of a saved master that has overrides, else ``None``."""

        if self._state.adding or self.pk is None or self.series_id is not None:
            return None
        rows = type(self)._base_manager.filter(pk=self.pk)
        if not type(self)._base_manager.filter(series_id=self.pk).exists():
            return None
        return rows.values_list("start", "all_day", "recurrence").first()

    def lock_subject(self, members: Any = None) -> Self:
        """Lock the calendar before this event: the calendar row serializes its overlap rule."""

        self.calendar.lock_subject()
        return super().lock_subject(members)

    def rebase_overrides(self, *, previous_start: datetime, previous_zone: str) -> None:
        """Carry this master's overrides onto its changed schedule, in the caller's transaction.

        Each ``original_start`` moves by the master's local wall-clock change, from
        ``previous_start`` in ``previous_zone`` to the current ``start`` in the
        calendar's zone. An override that still names an instance keeps it; one that
        no longer does is detached as a standalone event when it carries edits, so
        nothing is lost, and deleted when it is a cancellation. Overrides move in the
        shift's direction, so two never claim the same instance at once.
        """

        old_tz, zone = get_zone_info(previous_zone), self.calendar.timezone
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
            elif override.status == EventStatus.CANCELLED:
                override.delete()
            else:
                override.series, override.original_start = None, None
                override.allow_immutable_save("series_id", "original_start")
                override.save(update_fields=["series", "original_start", "updated_at"])

    def _overlap_rows(self) -> CalendarEventQuerySet:
        """The rows an overlap is judged against, read only as far as this save may see.

        Every row, as system work, when nothing pins an actor or a bypass is in
        effect, or once the library's public save check (``check_save``) admits this
        save; otherwise the actor's own read scope, so a refused save learns nothing
        it could not read.
        """

        actor, unscoped = self.effective_actor()
        if unscoped or actor is None:
            return type(self).system_queryset()
        try:
            admitted = self.check_save().allowed
        except (ObjectDoesNotExist, TypeError, ValidationError, ValueError):
            admitted = False
        return type(self).system_queryset() if admitted else type(self).objects.with_actor(actor)

    def _refuse_overlap(self) -> None:
        """Refuse this busy event when it meets another busy occurrence on a ``refuse`` calendar.

        Only this row's own ``[start, end)`` is checked (a master's first instance).
        This row's stored copy, a master's own overrides, and the instance an
        override replaces are not others. The refusal names no other event.
        """

        if self.calendar_id is None or self.start is None or self.end is None or self.end <= self.start:
            return
        if self.status == EventStatus.CANCELLED or self.transparency == EventTransparency.FREE:
            return
        if self.calendar.overlap_policy != OverlapPolicy.REFUSE:
            return
        rows = self._overlap_rows().filter(calendar_id=self.calendar_id)
        for moment in rows.moments(self.start, self.end):
            other = moment.event
            if self.pk is not None and (other.pk == self.pk or other.series_id == self.pk):
                continue
            if other.pk == self.series_id and moment.start == self.original_start:
                continue
            if other.transparency == EventTransparency.BUSY:
                raise ValidationError({"start": OVERLAP_MESSAGE})

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

    def occurrence_id(self, start: datetime) -> str:
        """Return the stable id of this row's occurrence at ``start`` (see :class:`EventOccurrence`)."""

        if not self.recurrence:
            return self.sqid
        return f"{self.sqid}:{start.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}"

    def expand_occurrences(self, window_start: datetime, window_end: datetime) -> list[EventOccurrence]:
        """Return this row's series occurrences in ``[window_start, window_end)``, as system work.

        The series is this row and its overrides (a master's, or an override's own
        single row), projected unredacted through
        :meth:`CalendarEventQuerySet.occurrences`; a caller that already holds the
        row reads its schedule.
        """

        series = type(self).system_queryset().filter(Q(pk=self.pk) | Q(series_id=self.pk))
        return series.occurrences(window_start, window_end)

    def reschedule(self, start: datetime, end: datetime) -> Self:
        """Move this single row to new ``start``/``end``, validate, and persist with history.

        A recurring master refuses: its occurrences are dragged one instance at a
        time through an override, and the series itself is edited through the
        event form. The moved fields are validated before the write
        (:meth:`clean`: the interval, the series and the overlap policy), so an
        invalid move surfaces as a ``ValidationError``, never an ``IntegrityError``.
        The ordinary save joins the caller's changeset, so provenance owns the
        resulting field delta and reason.
        """

        if self.recurrence:
            raise ValidationError(
                {"start": "A recurring event's occurrences cannot be dragged; edit the series instead."}
            )
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
        ordering = ("event", "pk")
        rebac_resource_type = "calendar/event_attendee"
        constraints = (
            models.UniqueConstraint(fields=("event", "handle"), name="%(app_label)s_attendee_one_per_handle"),
            models.UniqueConstraint(
                fields=("event",),
                condition=Q(role="organizer"),
                name="%(app_label)s_attendee_one_organizer",
            ),
        )

    def __str__(self) -> str:
        """Return a readable label for Django displays."""

        return f"{self.event_id}:{self.handle_id}"
