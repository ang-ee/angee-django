"""Django config for the Angee scheduling addon.

Scheduling owns *time as values* for the platform: recurrence, due rules and
named zones. Its ``AppConfig`` exists so the addon's contract — including the face
it declares but has not yet built — has one authoritative home (App facts live on
``AppConfig``). Read :class:`SchedulingConfig` for that contract. Its fields
register their GraphQL scalars at field-module import.
"""

from __future__ import annotations

from django.apps import AppConfig


class SchedulingConfig(AppConfig):
    """The scheduling addon contract: the single owner of recurrence, due rules and working time.

    Every built face is a *value type*, not a record: no model, no sqid, no REBAC.
    Consumers compose its fields and pure value objects.

    **Recurrence face — built.** Scheduling owns RFC-5545 recurrence. Two objects
    carry it:

    - :class:`~angee.scheduling.fields.RecurrenceField` — a ``CharField`` holding
      an RRULE string whose ``full_clean`` rejects a malformed rule. A consumer
      model (e.g. a calendar event) composes it to mark itself recurring; blank
      means a single, non-recurring row.
    - :class:`~angee.scheduling.recurrence.Recurrence` — the value object that
      turns a stored rule into concrete occurrences.

    **Timezone / window contract** (the owner of "what day is it"):
    ``Recurrence.occurrences(dtstart, window_start, window_end, zone=...)`` takes
    and returns timezone-aware **UTC** datetimes (Django ``USE_TZ``), computes the
    expansion in the named IANA ``zone`` or, when it is blank, the project's
    ``TIME_ZONE``, and bounds it to the **half-open** window
    ``[window_start, window_end)`` (start inclusive, end exclusive). An all-day
    event — whose ``dtstart`` is local midnight stored as UTC — therefore expands
    **by calendar date in its zone**, DST included. An empty rule yields the
    single ``dtstart``. A zone is stored and exchanged as its IANA name
    (:class:`~angee.scheduling.fields.TimeZoneField`,
    :data:`~angee.scheduling.zones.ZoneName`), never as a ``tzinfo``.

    **Due-rule face — built.** Scheduling owns "when is this due":
    :class:`~angee.scheduling.due.DueRule` (named due anchors, calendar and counter
    offsets, alignment, ``earliest``/``latest`` terms, warn and grace windows,
    repeat) stored in a :class:`~angee.scheduling.fields.DueRuleField`. Its pure
    ``resolve`` returns a :class:`~angee.scheduling.due.Due` whose point the
    consumer writes into its own date or instant column and whose ``basis`` it
    stores beside it; ``Due.status`` is computed on read and never stored. The
    subject's owner supplies the anchors, counters and rates; scheduling holds
    none.

    **Working-time / resource-calendar face — owned here, not yet built.**
    Scheduling is also the single future owner of *working time*: a
    ``ResourceCalendar`` (per-resource attendance intervals) exposing
    ``working_intervals(window)`` for the "when is this resource available"
    question that HR shift planning and MRP capacity scheduling ask. It is
    **deferred to its first consumer (W4/W5 HR/MRP)**: building a resource
    calendar with no consumer would fail the just-in-time and deletion checks.
    Nothing for this face ships today; it is declared here so the ownership is
    unambiguous when that consumer arrives, and business-day due offsets arrive
    with it.
    """

    default = True
    name = "angee.scheduling"
