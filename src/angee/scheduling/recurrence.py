"""Recurrence: the RFC-5545 rule value object for the scheduling addon.

:class:`Recurrence` wraps the RRULE string a
:class:`~angee.scheduling.fields.RecurrenceField` stores and answers the
questions a caller has about it: *is this rule well-formed?*
(:meth:`Recurrence.validate`), *which concrete moments does it produce in a
window?* (:meth:`Recurrence.occurrences`) and *which moment comes next?*
(:meth:`Recurrence.get_occurrence_after`). It holds no state beyond the rule and touches no
database — recurrence is a value, not a record.

Parsing and expansion are delegated to ``python-dateutil`` (``rrulestr``), the
stack's owner of RFC-5545. The timezone/window contract is documented on
:meth:`Recurrence.occurrences` and on ``SchedulingConfig``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from functools import wraps
from itertools import islice, takewhile

from dateutil.parser import parse
from dateutil.rrule import rrule, rruleset, rrulestr
from django.core.exceptions import ValidationError

from angee.scheduling.zones import get_zone_info, require_aware

_VALIDATION_ANCHOR = datetime(2000, 1, 1, tzinfo=UTC)
"""A fixed aware anchor for grammar-only validation; its date never affects validity."""


def _rules(compiled: rrule | rruleset) -> list[rrule]:
    """The rules a parsed recurrence holds: itself, or every rule of a set (dateutil keeps them in ``_rrule``)."""

    return list(compiled._rrule) if isinstance(compiled, rruleset) else [compiled]


def _in_range[**P, R](method: Callable[P, R]) -> Callable[P, R]:
    """Refuse an expansion that reaches past the representable dates as a ``ValidationError``, never an overflow.

    A moment near year 1 or 9999 can leave Python's range once moved into a zone;
    the caller (a form, a sync's record) then refuses that one rule instead of
    failing whatever it was part of.
    """

    @wraps(method)
    def bounded(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return method(*args, **kwargs)
        except OverflowError as exc:
            raise ValidationError(
                "This recurrence reaches past the last representable date.", code="out_of_range"
            ) from exc

    return bounded


def _capped(moments: Iterable[datetime]) -> list[datetime]:
    """Collect ``moments``, refusing more than :attr:`Recurrence.OCCURRENCE_LIMIT` rather than truncating."""

    limit = Recurrence.OCCURRENCE_LIMIT
    collected = list(islice(moments, limit + 1))
    if len(collected) > limit:
        raise ValidationError(
            f"This recurrence yields more than {limit} occurrences.", code="too_many_occurrences"
        )
    return collected


class Recurrence:
    """An RFC-5545 RRULE and its bounded, timezone-aware expansion.

    Construct from the stored rule string; an empty string means "no recurrence"
    (a single event). The object is immutable-by-convention — it only reads the
    rule it was given. Legacy floating and date-only ``UNTIL`` values use
    the anchor's local timezone (date-only values mean local midnight).
    Explicit UTC bounds retain their instant.

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

    FREQUENCIES = ("YEARLY", "MONTHLY", "WEEKLY", "DAILY", "HOURLY", "MINUTELY", "SECONDLY")
    """RFC 5545 frequencies, coarsest first (python-dateutil's own order)."""

    OCCURRENCE_LIMIT = 50_000
    """The most occurrences one expansion yields; past it the expansion refuses rather than truncate."""

    def validate(self, *, finest: str | None = None) -> None:
        """Raise :class:`~django.core.exceptions.ValidationError` unless the rule parses and stays bounded.

        A blank rule is valid (non-recurring). Otherwise the rule is parsed for its
        grammar alone, anchored at a fixed date and the result discarded — validity
        is ``dtstart``-agnostic, so no start date is required to reject a malformed
        rule. ``finest`` (one of :attr:`FREQUENCIES`) refuses rules that repeat more
        often, and a ``COUNT`` over :attr:`OCCURRENCE_LIMIT` is refused.
        """

        if not self.rule:
            return
        try:
            rules = _rules(self._compile(_VALIDATION_ANCHOR))
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValidationError(
                "Enter a valid RFC-5545 recurrence rule (RRULE).",
                code="invalid",
            ) from exc
        if finest is not None and any(rule._freq > self.FREQUENCIES.index(finest) for rule in rules):
            raise ValidationError(
                f"Choose a rule that repeats at most {finest.lower()}.", code="too_frequent"
            )
        if any(rule._count is not None and rule._count > self.OCCURRENCE_LIMIT for rule in rules):
            raise ValidationError(
                f"A rule repeats at most {self.OCCURRENCE_LIMIT} times.", code="too_many_occurrences"
            )

    @_in_range
    def occurrences(
        self,
        dtstart: datetime,
        window_start: datetime,
        window_end: datetime,
        *,
        zone: str = "",
    ) -> list[datetime]:
        """Return the occurrences within ``[window_start, window_end)`` as aware UTC.

        ``dtstart`` and the window bounds are timezone-aware datetimes (canonically
        UTC); the returned occurrences are aware UTC. A **naive** ``dtstart`` or
        window bound is rejected with :class:`~django.core.exceptions.ValidationError`
        rather than silently reinterpreted in the project timezone — "what day is
        it" has one owner and it will not guess an offset. Expansion runs in
        ``zone``, an IANA name (a venue's or a schedule's), or in the project's
        ``TIME_ZONE`` when it is blank: the rule is unrolled against the local
        wall-clock projection of ``dtstart`` in that zone, so a 09:00 event stays
        09:00 local across a DST shift and an all-day event (local-midnight
        ``dtstart``) steps by calendar date in that zone. The window is
        **half-open** — ``window_start`` is included, ``window_end`` excluded. An
        empty rule yields the single ``dtstart`` when it falls in the window. A
        window holding more than :attr:`OCCURRENCE_LIMIT` occurrences is refused
        with a ``ValidationError``, never truncated, as is one that reaches past the
        representable dates.
        """

        require_aware(dtstart=dtstart, window_start=window_start, window_end=window_end)
        tz = get_zone_info(zone)
        after = window_start.astimezone(tz)
        before = window_end.astimezone(tz)
        anchor = dtstart.astimezone(tz)
        if self.rule:
            upcoming = self._compile(anchor).xafter(after, inc=True)
            candidates = _capped(takewhile(lambda moment: moment < before, upcoming))
        else:
            candidates = [anchor]
        return [
            moment.astimezone(UTC)
            for moment in candidates
            if after <= moment < before
        ]

    @_in_range
    def get_occurrence_after(self, dtstart: datetime, moment: datetime, *, zone: str = "") -> datetime | None:
        """Return the first occurrence strictly after ``moment`` as aware UTC, or ``None`` once the rule ends.

        The series starts at ``dtstart`` and unrolls in ``zone`` (blank: the project
        ``TIME_ZONE``), exactly as :meth:`occurrences` expands it; ``moment`` need
        not be an occurrence. An empty rule is the single ``dtstart``. This is
        dateutil's native ``rrule.after``, so a rule with no ``UNTIL`` or ``COUNT``
        never ends.
        """

        require_aware(dtstart=dtstart, moment=moment)
        tz = get_zone_info(zone)
        anchor = dtstart.astimezone(tz)
        if not self.rule:
            following: datetime | None = anchor if anchor > moment else None
        else:
            following = self._compile(anchor).after(moment.astimezone(tz), inc=False)
        return following.astimezone(UTC) if following is not None else None

    @_in_range
    def get_last_occurrence(self, dtstart: datetime, *, zone: str = "") -> datetime | None:
        """Return the series' last occurrence as aware UTC, or ``None`` when the rule never ends.

        A rule ends when every rule it parses to carries ``UNTIL`` or ``COUNT``; a
        set with one rule that has neither repeats for ever. An empty rule is the
        single ``dtstart``. The series unrolls in ``zone`` (blank: the project
        ``TIME_ZONE``), as :meth:`occurrences` expands it. A series of more than
        :attr:`OCCURRENCE_LIMIT` occurrences is refused with a ``ValidationError``.
        """

        require_aware(dtstart=dtstart)
        tz = get_zone_info(zone)
        anchor = dtstart.astimezone(tz)
        if not self.rule:
            return anchor.astimezone(UTC)
        compiled = self._compile(anchor)
        if not all(rule._count is not None or rule._until is not None for rule in _rules(compiled)):
            return None
        series = _capped(compiled)
        return series[-1].astimezone(UTC) if series else None

    def _compile(self, anchor: datetime) -> rrule | rruleset:
        """Parse the rule anchored at an aware local ``anchor`` (a dateutil rrule/rruleset).

        A floating ``UNTIL`` past the last representable UTC instant (9999-12-31 in
        a zone behind UTC) ends with the representable range.
        """

        def local_until(match: re.Match[str]) -> str:
            # Dateutil remains the date grammar owner; adapt only the legacy
            # floating cutoff to the aware DTSTART contract it requires.
            until = parse(match[1], default=anchor.replace(tzinfo=None, hour=0, minute=0, second=0))
            if until.tzinfo is None:
                try:
                    bound = until.replace(tzinfo=anchor.tzinfo).astimezone(UTC)
                except OverflowError:
                    bound = datetime.max.replace(tzinfo=UTC)
                return "UNTIL=" + bound.strftime("%Y%m%dT%H%M%SZ")
            return match[0]

        rule = re.sub(r"(?im)(?<![A-Z])UNTIL=([^;\s]+)", local_until, self.rule)
        return rrulestr(rule, dtstart=anchor)
