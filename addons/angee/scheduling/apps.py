"""Django config for the Angee scheduling addon.

Scheduling owns *time recurrence* for the platform. Its one ``ready()`` seam
teaches the GraphQL runtime how ``RecurrenceField`` projects under ``auto`` (so a
consumer writes ``recurrence: auto``, not a hand-annotated wire type); the
``AppConfig`` also exists so the addon's contract — including the face it declares
but has not yet built — has one authoritative home (App facts live on
``AppConfig``). Read :class:`SchedulingConfig` for that contract.
"""

from __future__ import annotations

from django.apps import AppConfig


class SchedulingConfig(AppConfig):
    """The scheduling addon contract: the single owner of recurrence and working time.

    **Recurrence face — built (this addon's whole surface today).** Scheduling
    owns RFC-5545 recurrence as a *value type*, not a record: no model, no sqid,
    no REBAC. Two objects carry it, both pure and both composed by consumers:

    - :class:`~angee.scheduling.fields.RecurrenceField` — a ``CharField`` holding
      an RRULE string whose ``full_clean`` rejects a malformed rule. A consumer
      model (e.g. a calendar event) composes it to mark itself recurring; blank
      means a single, non-recurring row.
    - :class:`~angee.scheduling.recurrence.Recurrence` — the value object that
      turns a stored rule into concrete occurrences.

    **Timezone / window contract** (the owner of "what day is it"):
    ``Recurrence.occurrences(dtstart, window_start, window_end)`` takes and returns
    timezone-aware **UTC** datetimes (Django ``USE_TZ``), computes the expansion in
    the project's ``TIME_ZONE``, and bounds it to the **half-open** window
    ``[window_start, window_end)`` (start inclusive, end exclusive). An all-day
    event — whose ``dtstart`` is midnight in ``TIME_ZONE`` stored as UTC —
    therefore expands **by calendar date in the project timezone**, DST included.
    An empty rule yields the single ``dtstart``.

    **Working-time / resource-calendar face — owned here, not yet built.**
    Scheduling is also the single future owner of *working time*: a
    ``ResourceCalendar`` (per-resource attendance intervals) exposing
    ``working_intervals(window)`` for the "when is this resource available"
    question that HR shift planning and MRP capacity scheduling ask. It is
    **deferred to its first consumer (W4/W5 HR/MRP)**: building a resource
    calendar with no consumer would fail the just-in-time and deletion checks.
    Nothing for this face ships today; it is declared here so the ownership is
    unambiguous when that consumer arrives.
    """

    default = True
    name = "angee.scheduling"

    def ready(self) -> None:
        """Register ``RecurrenceField``'s GraphQL wire type before any schema builds.

        strawberry-django resolves a model field to its GraphQL type through an
        exact-class map (no MRO walk), so a ``CharField`` subclass like
        ``RecurrenceField`` needs an explicit entry to project as ``String`` under
        ``auto`` — the same registration ``angee.graphql`` performs for its own value
        fields. Doing it here, through the ``angee.graphql`` owner seam, is what earns
        this addon's ``angee.graphql`` dependency and keeps every consumer's
        ``recurrence`` column annotation-free.
        """

        super().ready()
        # Phase-1 ready hook: defer the GraphQL/runtime imports until after app
        # population, mirroring the sibling base-addon ready() seams.
        from angee.graphql.field_types import register_field_type
        from angee.scheduling.fields import RecurrenceField

        register_field_type(RecurrenceField, str)
