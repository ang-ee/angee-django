"""GraphQL schema contributions for calendars, events and attendees.

:class:`Calendar`, :class:`CalendarEvent` and :class:`EventAttendee` are writable
console resources (list, by_pk, insert, update, delete). Auto-CRUD lists return
stored rows (an event list returns masters and overrides); the authored
``event_occurrences(window_start, window_end, calendars)`` query returns what a
calendar shows, expanded server-side through
:meth:`~angee.calendar.models.CalendarEventQuerySet.occurrences` over the actor's
``see_busy`` scope, with busy-only occurrences for the events the actor may not
read. Sharing is the generic record-access surface over each calendar's
``rebac_grantable`` relations.

``reschedule_event`` is the drag verb: it preflights the row's ``write`` and
dispatches to :meth:`~angee.calendar.models.CalendarEvent.reschedule`, which owns
the refusals (a recurring master, an inverted interval, an overlap); the shared
``action_guard`` projects them to an in-band ``ActionResult``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.db import transaction
from rebac import ObjectRef
from rebac.field_visibility import check_field_access
from strawberry import auto

from angee.calendar.models import CalendarEventQuerySet
from angee.graphql.access import ChangeReadGate
from angee.graphql.actions import ActionResult, action_guard, authorized_action_target, session_user
from angee.graphql.capabilities import permissions_field
from angee.graphql.data.hasura import AngeeHasuraWriteBackend, hasura_model_resource, public_pk_decoder
from angee.graphql.events import ChangeEvent, ChangePayload
from angee.graphql.ids import PublicID
from angee.graphql.node import NODE_DISPLAY_NAME_DESCRIPTION, AngeeNode
from angee.graphql.relations import actor_scoped_to_one
from angee.graphql.subscriptions import changes
from angee.iam.schema import UserType
from angee.parties.schema import HandleType, PartyType
from angee.tags.schema import TaggedNode, tags_input_extensions

Calendar = apps.get_model("calendar", "Calendar")
CalendarEvent = apps.get_model("calendar", "CalendarEvent")
EventAttendee = apps.get_model("calendar", "EventAttendee")
Party = apps.get_model("parties", "Party")
Handle = apps.get_model("parties", "Handle")


# ---------------------------------------------------------------------------
# Node projections
# ---------------------------------------------------------------------------


@strawberry_django.type(Calendar)
class CalendarType(AngeeNode):
    """Projection of one calendar and whose it is."""

    display_name: str = strawberry_django.field(
        resolver=AngeeNode.display_name, only=["name"], description=NODE_DISPLAY_NAME_DESCRIPTION
    )
    name: auto
    description: auto
    color: auto
    timezone: auto
    overlap_policy: auto
    created_at: auto
    updated_at: auto
    party: PartyType | None = actor_scoped_to_one("party")
    owner: UserType | None = actor_scoped_to_one("owner")
    permissions = permissions_field(("write", "share", "delete", "transfer"))


@strawberry_django.type(CalendarEvent)
class CalendarEventType(TaggedNode, AngeeNode):
    """Projection of one stored event: a master, an override or a plain event (not its occurrences)."""

    display_name: str = strawberry_django.field(
        resolver=AngeeNode.display_name, only=["title", "sqid"], description=NODE_DISPLAY_NAME_DESCRIPTION
    )
    title: auto
    description: auto
    location: auto
    start: auto
    end: auto
    all_day: auto
    recurrence: auto
    timezone: auto
    original_start: auto
    status: auto
    transparency: auto
    visibility: auto
    conference_url: auto
    created_at: auto
    updated_at: auto
    calendar: CalendarType | None = actor_scoped_to_one("calendar")
    series: CalendarEventType | None = actor_scoped_to_one("series")
    permissions = permissions_field(("write", "delete"))


@strawberry_django.type(EventAttendee)
class EventAttendeeType(AngeeNode):
    """Projection of one attendee: the event, the invited handle, its role and response."""

    role: auto
    response: auto
    created_at: auto
    updated_at: auto
    event: CalendarEventType | None = actor_scoped_to_one("event")
    handle: HandleType | None = actor_scoped_to_one("handle")


@strawberry.type
class EventOccurrenceType:
    """One occurrence a calendar shows, as ``event_occurrences`` returns it.

    A server-expanded occurrence, not a stored row: the resolver returns
    :class:`~angee.calendar.models.EventOccurrence` values, which own the id
    shapes. ``event_sqid`` opens the stored row, ``calendar`` is the calendar's
    public id, ``editable`` gates drag, and a ``redacted`` occurrence is busy time
    whose details the actor may not read.
    """

    occurrence_id: str
    event_sqid: str
    calendar: PublicID
    title: str
    start: datetime
    end: datetime
    all_day: bool
    editable: bool
    redacted: bool


class FreeBusyChangeReadGate(ChangeReadGate):
    """Readers receive an event's change as usual; free/busy viewers learn only that its busy time changed.

    A subscriber who may ``see_busy`` an event but not ``read`` it gets the change
    with no changed fields, values or related records: enough to refetch its
    occurrences, which the occurrence read redacts.
    """

    def filter(self, payload: Mapping[str, Any] | ChangePayload) -> ChangeEvent | None:
        """Return the reader's event, the free/busy viewer's bare event, or ``None``."""

        change = self._change(payload)
        event = super().filter(change)
        if event is not None or not self.resource_type:
            return event
        resource = change.read_resource or ObjectRef(self.resource_type, change.resource_identifier)
        busy = check_field_access(self.active_backend, subject=self.actor, action="see_busy", resource=resource)
        if not busy.allowed:
            return None
        return ChangeEvent.from_payload(replace(change, changed_fields=None, changed_values=None, related_records=()))


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


_CALENDAR_RESOURCE = hasura_model_resource(
    CalendarType,
    model=Calendar,
    name="calendars",
    filterable=["id", "party", "name", "overlap_policy"],
    sortable=["name", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["party", "overlap_policy"],
    # Whose calendar it is is set once: its holder and its access follow the party.
    insertable=["party", "name", "description", "color", "timezone", "overlap_policy"],
    updatable=["name", "description", "color", "timezone", "overlap_policy"],
    field_id_decode={"party": public_pk_decoder(Party)},
    write_backend=AngeeHasuraWriteBackend(Calendar, public_id_fields=("party",)),
    id_column="sqid",
)

_EVENT_SCHEDULE_FIELDS = (
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

_EVENT_RESOURCE = hasura_model_resource(
    CalendarEventType,
    model=CalendarEvent,
    name="calendar_events",
    filterable=["id", "calendar", "series", "start", "end", "all_day", "status", "transparency", "visibility"],
    sortable=["start", "end", "title", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["calendar", "status", "all_day"],
    # Containment is set once (`CalendarEvent.immutable_fields`): an event never
    # moves to another calendar, series or instance after insert.
    insertable=["calendar", "series", "original_start", *_EVENT_SCHEDULE_FIELDS],
    updatable=list(_EVENT_SCHEDULE_FIELDS),
    field_id_decode={
        "calendar": public_pk_decoder(Calendar),
        "series": public_pk_decoder(CalendarEvent),
    },
    write_backend=AngeeHasuraWriteBackend(CalendarEvent, public_id_fields=("calendar", "series")),
    id_column="sqid",
)

_ATTENDEE_RESOURCE = hasura_model_resource(
    EventAttendeeType,
    model=EventAttendee,
    name="event_attendees",
    filterable=["id", "event", "handle", "role", "response"],
    sortable=["created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["event", "handle", "role", "response"],
    # An attendee belongs to one event for good (`EventAttendee.immutable_fields`).
    insertable=["event", "handle", "role", "response"],
    updatable=["handle", "role", "response"],
    field_id_decode={
        "event": public_pk_decoder(CalendarEvent),
        "handle": public_pk_decoder(Handle),
    },
    write_backend=AngeeHasuraWriteBackend(EventAttendee, public_id_fields=("event", "handle")),
    id_column="sqid",
)


# ---------------------------------------------------------------------------
# Authored occurrence query and drag verb
# ---------------------------------------------------------------------------


@strawberry.type
class CalendarQuery:
    """The occurrences a calendar shows, expanded server-side."""

    @strawberry.field
    def event_occurrences(
        self,
        info: strawberry.Info,
        window_start: datetime,
        window_end: datetime,
    ) -> list[EventOccurrenceType]:
        """Return the actor's occurrences in ``[window_start, window_end)``.

        Aware UTC bounds, start inclusive and end exclusive, at most 400 days apart;
        every occurrence that meets the window is returned, in each calendar's zone.
        """

        scoped = cast(CalendarEventQuerySet, CalendarEvent.objects.with_actor(session_user(info)))
        return cast(list[EventOccurrenceType], scoped.occurrences(window_start, window_end))


@strawberry.type
class CalendarEventActionMutation:
    """The calendar's drag-reschedule verb."""

    @strawberry.mutation
    @action_guard("Reschedule failed.")
    def reschedule_event(
        self,
        info: strawberry.Info,
        id: PublicID,
        start: datetime,
        end: datetime,
    ) -> ActionResult:
        """Move one event row's ``start``/``end`` after the ``write`` preflight."""

        event = authorized_action_target(info, CalendarEvent, id, "write")
        with transaction.atomic():
            event.reschedule(start, end)
        return ActionResult(ok=True, message="Event rescheduled.", id=event.sqid)


_RESOURCES = (_CALENDAR_RESOURCE, _EVENT_RESOURCE, _ATTENDEE_RESOURCE)

schemas = {
    "console": {
        "query": [*[resource.query for resource in _RESOURCES], CalendarQuery],
        "mutation": [*[resource.mutation for resource in _RESOURCES], CalendarEventActionMutation],
        "subscription": [
            changes(Calendar, field="calendarChanged"),
            changes(CalendarEvent, field="calendarEventChanged", read_gate=FreeBusyChangeReadGate),
            changes(EventAttendee, field="eventAttendeeChanged"),
        ],
        "types": [
            CalendarType,
            CalendarEventType,
            EventAttendeeType,
            EventOccurrenceType,
            *[type_ for resource in _RESOURCES for type_ in resource.types],
        ],
        "input_extensions": tags_input_extensions(_EVENT_RESOURCE),
    },
}
