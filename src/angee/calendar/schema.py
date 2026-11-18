"""GraphQL schema contributions for calendars, events and attendees.

:class:`Calendar`, :class:`CalendarEvent` and :class:`EventAttendee` are writable
console resources (list, by_pk, insert, update, delete). Auto-CRUD lists return
stored rows (an event list returns masters and overrides); the authored
``event_occurrences(window_start, window_end)`` query returns what a
calendar shows, expanded server-side through
:meth:`~angee.calendar.models.CalendarEventQuerySet.occurrences` over the actor's
``see_busy`` scope, with busy-only occurrences for the events the actor may not
read. Sharing is the generic record-access surface over each calendar's
``rebac_grantable`` relations.

``reschedule_event`` is the drag verb: it preflights the row's ``write`` and
dispatches to :meth:`~angee.calendar.models.CalendarEvent.reschedule`, which owns
the refusals (a recurring master, an inverted interval, an overlap); the shared
``action_guard`` projects them to an in-band ``ActionResult``.

:class:`~angee.calendar.models.CalendarSource` is a read-only console resource
(``calendar_sources``): a source addon's connect verb creates it, and its owner's
verbs ``sync_calendar_source`` (``write``) and ``unsubscribe_calendar_source``
(``delete``) dispatch to :meth:`~angee.calendar.models.CalendarSource.sync_now` and
:meth:`~angee.calendar.models.CalendarSource.unsubscribe`. A calendar projects its
``source`` and ``is_writable``; a read-only subscription refuses event writes in
the model.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from functools import partial
from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.db import transaction
from rebac import ObjectRef
from rebac.field_visibility import check_field_access
from rebac.resources import resource_id_attr
from strawberry import auto

from angee.calendar.backends import EVENT_FIELDS
from angee.calendar.models import Calendar as CalendarModel
from angee.calendar.models import CalendarEvent as CalendarEventModel
from angee.calendar.models import EventOccurrence, EventTransparency
from angee.graphql.access import ChangeReadGate
from angee.graphql.actions import ActionResult, action_guard, authorized_action_target, session_user
from angee.graphql.capabilities import permission_expression, permissions_field
from angee.graphql.data.hasura import AngeeHasuraWriteBackend, hasura_model_resource, public_pk_decoder
from angee.graphql.events import ChangeEvent, ChangePayload
from angee.graphql.ids import PublicID, require_instance_for_id
from angee.graphql.node import NODE_DISPLAY_NAME_DESCRIPTION, AngeeNode
from angee.graphql.relations import actor_scoped_to_one
from angee.graphql.subscriptions import changes
from angee.iam.schema import UserType
from angee.integrate.schema import BridgeTypeMixin, ConnectWithAccount
from angee.parties.schema import HandleType, PartyType
from angee.tags.schema import TaggedNode, tags_input_extensions

# The composed models, typed by the source contracts they compose (their managers and reads).
Calendar = cast("type[CalendarModel]", apps.get_model("calendar", "Calendar"))
CalendarSource = apps.get_model("calendar", "CalendarSource")
CalendarEvent = cast("type[CalendarEventModel]", apps.get_model("calendar", "CalendarEvent"))
EventAttendee = apps.get_model("calendar", "EventAttendee")
Party = apps.get_model("parties", "Party")
Handle = apps.get_model("parties", "Handle")


# ---------------------------------------------------------------------------
# Node projections
# ---------------------------------------------------------------------------


@strawberry_django.type(CalendarSource)
class CalendarSourceType(BridgeTypeMixin, AngeeNode):
    """Projection of a connected calendar source, such as a subscribed ICS feed.

    ``config`` carries only what may be shown (an ICS feed's label); a secret address
    is the source credential's. ``permissions`` gate its owner verbs.
    """

    poll_interval: auto
    permissions = permissions_field(("write", "delete"))


@strawberry.type
class CalendarSourceBackendType(ConnectWithAccount):
    """What connecting a source of one calendar backend asks for: the session user's accounts it reads with."""

    key: str
    label: str


@strawberry_django.type(Calendar)
class CalendarType(AngeeNode):
    """Projection of one calendar and whose it is."""

    source: CalendarSourceType | None = actor_scoped_to_one("source")

    @strawberry_django.field(select_related=["source"])
    def is_writable(self) -> bool:
        """Whether the calendar takes local edits: a read-only subscription does not."""

        return bool(cast(Any, self).is_writable)

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
    occurrences, which the occurrence read redacts. A free event is hidden from them
    as the occurrence read hides it, unless the partial save names its transparency.
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
        # A free event is no busy time, so free/busy viewers hear of it no more than the occurrence read shows it;
        # a partial save that names its transparency still reaches them, since a busy block may have gone.
        stored = self.model._base_manager.filter(**{resource_id_attr(self.model): change.resource_identifier})
        flipped = "transparency" in (change.changed_fields or ())
        if not flipped and stored.filter(transparency=EventTransparency.FREE).exists():
            return None
        return ChangeEvent.from_payload(replace(change, changed_fields=None, changed_values=None, related_records=()))


class CalendarEventWriteBackend(AngeeHasuraWriteBackend):
    """Insert an override with its master's visibility and transparency when the input omits them.

    Omission is a fact of the input, so it is decided here; a value the input or a
    sync supplies is kept as given (:meth:`CalendarEvent.series_defaults`).
    """

    def insert(self, info: strawberry.Info, data: dict[str, Any]) -> Any:
        """Fill the omitted override defaults from the master the actor reads, then insert."""

        omitted = [
            name for name in ("visibility", "transparency") if data.get(name, strawberry.UNSET) is strawberry.UNSET
        ]
        series = data.get("series")
        if series and omitted:
            readable = CalendarEvent.objects.with_actor(session_user(info))
            defaults = require_instance_for_id(CalendarEvent, str(series), queryset=readable).series_defaults()
            data = {**data, **{name: defaults[name] for name in omitted}}
        return super().insert(info, data)


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


_CALENDAR_RESOURCE = hasura_model_resource(
    CalendarType,
    model=Calendar,
    name="calendars",
    # `writable`: the calendars the actor may add events to, as the event form's picker offers them.
    filterable=["id", "party", "name", "overlap_policy", "writable"],
    filter_expressions={"writable": partial(permission_expression, name="write")},
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
    insertable=["calendar", "series", "original_start", *EVENT_FIELDS],
    updatable=list(EVENT_FIELDS),
    field_id_decode={
        "calendar": public_pk_decoder(Calendar),
        "series": public_pk_decoder(CalendarEvent),
    },
    write_backend=CalendarEventWriteBackend(CalendarEvent, public_id_fields=("calendar", "series")),
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
    """The occurrences a calendar shows, expanded server-side, and what connecting a source asks for."""

    @strawberry.field(graphql_type=list[EventOccurrenceType])
    def event_occurrences(
        self,
        info: strawberry.Info,
        window_start: datetime,
        window_end: datetime,
    ) -> list[EventOccurrence]:
        """Return the actor's occurrences in ``[window_start, window_end)``.

        Aware UTC bounds, start inclusive and end exclusive, at most 400 days apart;
        every occurrence that meets the window is returned, in each event's expansion zone.
        """

        return CalendarEvent.objects.with_actor(session_user(info)).occurrences(window_start, window_end)

    @strawberry.field
    def calendar_source_backend(self, info: strawberry.Info, backend_class: str) -> CalendarSourceBackendType:
        """Describe connecting a source of one backend for the signed-in user, before any source exists.

        The accounts are the user's own that the backend reads with
        (``Integration.connectable_credentials``); a source addon's connect verb
        takes one of them.
        """

        source = CalendarSource.objects.build(backend_class)
        impl = source.get_backend_class_impl()
        return CalendarSourceBackendType.of(source, session_user(info), key=impl.key, label=str(impl.label))


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


@strawberry.type
class CalendarActionMutation:
    """The verb that gives a user without a calendar their own."""

    @strawberry.mutation
    @action_guard("Creating your calendar failed.")
    def ensure_personal_calendar(self, info: strawberry.Info) -> ActionResult:
        """Return the signed-in user's personal calendar, creating it on first use."""

        calendar = Calendar.objects.ensure_personal(session_user(info))
        return ActionResult(ok=True, message="Your calendar is ready.", id=calendar.sqid)


@strawberry.type
class CalendarSourceActionMutation:
    """A connected calendar's owner verbs: sync it now, and unsubscribe."""

    @strawberry.mutation
    @action_guard("Sync failed.")
    def sync_calendar_source(self, info: strawberry.Info, id: PublicID) -> ActionResult:
        """Queue the source's sync after the ``write`` preflight (its owner, or a platform admin)."""

        source = authorized_action_target(info, CalendarSource, id, "write")
        queued = source.sync_now()
        return ActionResult(ok=True, message="Sync queued." if queued else "A sync is already running.", id=source.sqid)

    @strawberry.mutation
    @action_guard("Unsubscribe failed.")
    def unsubscribe_calendar_source(self, info: strawberry.Info, id: PublicID) -> ActionResult:
        """End the subscription after the ``delete`` preflight: disconnect it now and queue its purge."""

        source = authorized_action_target(info, CalendarSource, id, "delete")
        source.unsubscribe()
        return ActionResult(ok=True, message="Unsubscribed; its events are being removed.", id=source.sqid)


_SOURCE_RESOURCE = hasura_model_resource(
    CalendarSourceType,
    model=CalendarSource,
    name="calendar_sources",
    filterable=[
        "id",
        "display_name",
        "backend_class",
        "lifecycle",
        "runtime_status",
        "sync_stage",
        "last_sync_completed_at",
        "updated_at",
    ],
    sortable=["display_name", "backend_class", "lifecycle", "runtime_status", "last_sync_completed_at", "updated_at"],
    aggregatable=["id", "last_sync_items"],
    groupable=["backend_class", "lifecycle", "runtime_status", "sync_stage"],
    insert=False,
    update=False,
    delete=False,
)

_RESOURCES = (_CALENDAR_RESOURCE, _EVENT_RESOURCE, _ATTENDEE_RESOURCE, _SOURCE_RESOURCE)

schemas = {
    "console": {
        "query": [*[resource.query for resource in _RESOURCES], CalendarQuery],
        "mutation": [
            *[resource.mutation for resource in _RESOURCES],
            CalendarActionMutation,
            CalendarEventActionMutation,
            CalendarSourceActionMutation,
        ],
        "subscription": [
            changes(Calendar, field="calendarChanged"),
            changes(CalendarEvent, field="calendarEventChanged", read_gate=FreeBusyChangeReadGate),
            changes(EventAttendee, field="eventAttendeeChanged"),
            changes(CalendarSource, field="calendarSourceChanged"),
        ],
        "types": [
            CalendarType,
            CalendarEventType,
            EventAttendeeType,
            EventOccurrenceType,
            CalendarSourceType,
            CalendarSourceBackendType,
            *[type_ for resource in _RESOURCES for type_ in resource.types],
        ],
        "input_extensions": tags_input_extensions(_EVENT_RESOURCE),
    },
}
