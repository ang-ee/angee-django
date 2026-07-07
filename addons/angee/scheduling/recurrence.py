"""Recurrence: the RFC-5545 rule value object for the scheduling addon.

:class:`Recurrence` wraps the RRULE string a
:class:`~angee.scheduling.fields.RecurrenceField` stores and answers the two
questions a caller has about it: *is this rule well-formed?*
(:meth:`Recurrence.validate`) and *which concrete moments does it produce in a
window?* (:meth:`Recurrence.occurrences`). It holds no state beyond the rule and
touches no database — recurrence is a value, not a record.

Parsing and expansion are delegated to ``python-dateutil`` (``rrulestr``), the
stack's owner of RFC-5545. The timezone/window contract is documented on
:meth:`Recurrence.occurrences` and on ``SchedulingConfig``.
"""

from __future__ import annotations

from datetime import UTC, datetime, tzinfo

from dateutil.rrule import rrule, rruleset, rrulestr
from django.core.exceptions import ValidationError
from django.utils import timezone

_VALIDATION_ANCHOR = datetime(2000, 1, 1)
"""A fixed naive anchor for grammar-only validation; its date never affects validity."""


class Recurrence:
    """An RFC-5545 RRULE and its bounded, timezone-aware expansion.

    Construct from the stored rule string; an empty string means "no recurrence"
    (a single event). The object is immutable-by-convention — it only reads the
    rule it was given.

    **Deviation kept for v1 — DTSTART must synchronize with the rule.** ``dateutil``
    follows the interpretation in which a ``BY*`` part that excludes the ``dtstart``
    slot drops ``dtstart`` from the set: a Tuesday ``dtstart`` with
    ``FREQ=WEEKLY;BYDAY=MO`` yields Mondays and never the stored Tuesday. Some
    readings of RFC-5545 instead force ``DTSTART`` to be the first instance. We keep
    ``dateutil``'s native semantics for v1 — recurrence is the ``python-dateutil``
    lock's shape and we do not post-process the occurrence set — so authors should
    write a rule whose ``BY*`` parts include the start. The behaviour is a chosen
    default, pinned by a test (``DtstartExclusionTests``), not an accident.
    """

    def __init__(self, rule: str = "") -> None:
        """Wrap ``rule``; a blank/absent rule is a valid, non-recurring recurrence."""

        self.rule = (rule or "").strip()

    def validate(self) -> None:
        """Raise :class:`~django.core.exceptions.ValidationError` unless the rule parses.

        A blank rule is valid (non-recurring). Otherwise the rule is parsed for its
        grammar alone, anchored at a fixed date and the result discarded — validity
        is ``dtstart``-agnostic, so no start date is required to reject a malformed
        rule.
        """

        if not self.rule:
            return
        try:
            self._compile(_VALIDATION_ANCHOR)
        except (ValueError, TypeError) as exc:
            raise ValidationError(
                "Enter a valid RFC-5545 recurrence rule (RRULE).",
                code="invalid",
            ) from exc

    def occurrences(
        self,
        dtstart: datetime,
        window_start: datetime,
        window_end: datetime,
    ) -> list[datetime]:
        """Return the occurrences within ``[window_start, window_end)`` as aware UTC.

        ``dtstart`` and the window bounds are timezone-aware datetimes (canonically
        UTC); the returned occurrences are aware UTC. A **naive** ``dtstart`` or
        window bound is rejected with :class:`~django.core.exceptions.ValidationError`
        rather than silently reinterpreted in the project timezone — "what day is
        it" has one owner and it will not guess an offset. Expansion runs in the
        project's ``TIME_ZONE``: the rule is unrolled against the local wall-clock
        projection of ``dtstart``, so a 09:00 event stays 09:00 local across a DST
        shift and an all-day event (local-midnight ``dtstart``) steps by calendar
        date in that timezone. The window is **half-open** — ``window_start`` is
        included, ``window_end`` excluded. An empty rule yields the single
        ``dtstart`` when it falls in the window.
        """

        _require_aware(dtstart=dtstart, window_start=window_start, window_end=window_end)
        tz = timezone.get_default_timezone()
        after = _to_local_naive(window_start, tz)
        before = _to_local_naive(window_end, tz)
        anchor = _to_local_naive(dtstart, tz)
        if self.rule:
            candidates = self._compile(anchor).between(after, before, inc=True)
        else:
            candidates = [anchor]
        return [
            timezone.make_aware(naive, tz).astimezone(UTC)
            for naive in candidates
            if after <= naive < before
        ]

    def _compile(self, anchor: datetime) -> rrule | rruleset:
        """Parse the rule anchored at a naive local ``anchor`` (a dateutil rrule/rruleset)."""

        return rrulestr(self.rule, dtstart=anchor)


def _require_aware(**moments: datetime) -> None:
    """Raise :class:`~django.core.exceptions.ValidationError` if any moment is naive.

    Expansion projects each bound into the project timezone; a naive datetime has
    no offset to project, so accepting one would silently reinterpret it in the
    system zone. Reject it at the door instead, naming the offending argument.
    """

    naive = [name for name, moment in moments.items() if timezone.is_naive(moment)]
    if naive:
        raise ValidationError(
            f"Recurrence expansion needs timezone-aware datetimes; {', '.join(sorted(naive))} "
            "is naive.",
            code="naive_datetime",
        )


def _to_local_naive(moment: datetime, tz: tzinfo) -> datetime:
    """Project an aware datetime into ``tz`` and drop the offset (naive local wall time)."""

    return moment.astimezone(tz).replace(tzinfo=None)
