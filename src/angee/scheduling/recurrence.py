"""Recurrence: the RFC-5545 rule value object for the scheduling addon.

:class:`Recurrence` wraps the RRULE string a
:class:`~angee.scheduling.fields.RecurrenceField` stores and answers the
questions a caller has about it: *is this rule well-formed?*
(:meth:`Recurrence.validate`), *which concrete moments does it produce in a
window?* (:meth:`Recurrence.occurrences`, :meth:`Recurrence.occurrences_after`),
*which moment comes next?* (:meth:`Recurrence.get_occurrence_after`) and *where does
the series end?* (:meth:`Recurrence.get_last_occurrence`). It holds no state
beyond the rule and touches no database — recurrence is a value, not a record.

Every expansion is bounded. ``dateutil`` checks a rule's end only at a candidate, so
a rule whose day filters can never match (``BYMONTH=2;BYMONTHDAY=30``) would scan to
year 9999; such a rule is recognised once, in one Gregorian cycle, and expands to
nothing. Given a start, :meth:`Recurrence.validate` refuses a rule with no occurrence
within about 30 years of it (``never_occurs``), probed in bounded time. One cap,
:data:`MAX_OCCURRENCES`, bounds every expansion and every ``COUNT``:
:meth:`Recurrence.occurrences` and :meth:`Recurrence.get_last_occurrence` count every
occurrence from ``DTSTART``, not only those in the window, and refuse a series that
passes it (``series_too_long``); :meth:`Recurrence.occurrences_after` refuses more
unless the caller pages with ``limit``. An expansion that reaches past the
representable dates is refused (``out_of_range``), never overflowed.

Parsing and expansion are delegated to ``python-dateutil`` (``rrulestr``), the
stack's owner of RFC-5545. The timezone/window contract is documented on
:meth:`Recurrence.occurrences` and on ``SchedulingConfig``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime, timedelta
from functools import cache, wraps

from dateutil.parser import parse
from dateutil.rrule import rrule, rruleset, rrulestr
from django.core.exceptions import ValidationError

from angee.scheduling.zones import get_zone_info, require_aware

_VALIDATION_ANCHOR = datetime(2000, 1, 1, tzinfo=UTC)
"""A fixed aware anchor for grammar-only validation; its date never affects validity."""

MAX_OCCURRENCES = 10_000
"""The most occurrences one expansion yields and a rule's ``COUNT`` may name; past it the
expansion refuses rather than truncate (:meth:`Recurrence.occurrences_after` lets a caller page)."""

_CYCLE_ANCHOR = datetime(datetime.max.year - 400, 1, 1, tzinfo=UTC)
"""The start of the last full Gregorian cycle (400 years) dateutil iterates before its year limit."""

_DAY_PARTS = frozenset({"BYMONTH", "BYMONTHDAY", "BYYEARDAY", "BYWEEKNO", "BYDAY", "WKST"})
"""The rule parts that choose days; a probe of these alone decides whether any day ever matches."""

NEVER_OCCURS_HORIZON = timedelta(days=30 * 366)
"""How far after its start a rule must first occur (about 30 years); a rule that does not never occurs."""

_GREGORIAN_CYCLE_YEARS = 400
"""The Gregorian calendar repeats every 400 years: weekdays, leap days and week numbers alike."""


@cache
def _matches_no_day(rule: str) -> bool:
    """Whether a single RRULE's day filters can never select a day.

    The calendar repeats every 400 years, so the day filters alone, unrolled yearly
    over the cycle that ends at dateutil's year limit, match some day exactly when
    they match one ever. A rule this cannot decide (an RRULE set, an ordinal
    ``BYDAY`` or ``BYSETPOS``, whose meaning depends on the frequency) is not
    refused here.
    """

    if "\n" in rule or ":" in rule:
        return False
    parts = dict(part.split("=", 1) for part in rule.upper().split(";") if "=" in part)
    if "BYSETPOS" in parts or re.search(r"[+-]?\d", parts.get("BYDAY", "")):
        return False
    probe = ";".join(["FREQ=YEARLY", *(f"{key}={value}" for key, value in sorted(parts.items()) if key in _DAY_PARTS)])
    return rrulestr(probe, dtstart=_CYCLE_ANCHOR).after(_CYCLE_ANCHOR, inc=True) is None


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


def _from_start(moments: Iterable[datetime], before: datetime | None = None) -> Iterator[datetime]:
    """Yield a series' occurrences from its start, stopping at ``before``, counting every one against the cap.

    Past :data:`MAX_OCCURRENCES` occurrences from the start the series refuses
    (``series_too_long``): a window far from the start costs as much as the
    occurrences before it, so the cap bounds the walk, not only what it returns.
    """

    for index, moment in enumerate(moments):
        if before is not None and moment >= before:
            return
        if index >= MAX_OCCURRENCES:
            raise ValidationError(
                f"This recurrence passes {MAX_OCCURRENCES} occurrences from its start.", code="series_too_long"
            )
        yield moment


def _open_ended(rule: str) -> str:
    """Return ``rule`` without its ``UNTIL`` and ``COUNT`` parts, which only end it sooner."""

    return "\n".join(
        ";".join(part for part in line.split(";") if part and not part.upper().startswith(("UNTIL=", "COUNT=")))
        for line in rule.splitlines()
    )


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

    def validate(self, *, finest: str | None = None, dtstart: datetime | None = None, zone: str = "") -> None:
        """Raise :class:`~django.core.exceptions.ValidationError` unless the rule parses and stays bounded.

        A blank rule is valid (non-recurring). Otherwise the rule is parsed for its
        grammar alone, anchored at a fixed date and the result discarded — validity
        is ``dtstart``-agnostic, so no start date is required to reject a malformed
        rule. ``finest`` (one of :attr:`FREQUENCIES`) refuses rules that repeat more
        often, and a ``COUNT`` over :data:`MAX_OCCURRENCES` is refused. Given the
        series' ``dtstart`` (expanded in ``zone``), a rule with no occurrence within
        :data:`NEVER_OCCURS_HORIZON` of it is refused too (``never_occurs``).
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
        if any(rule._count is not None and rule._count > MAX_OCCURRENCES for rule in rules):
            raise ValidationError(
                f"A rule repeats at most {MAX_OCCURRENCES} times.", code="too_many_occurrences"
            )
        if dtstart is not None and self.never_occurs(dtstart, zone=zone):
            raise ValidationError(
                "This recurrence has no occurrence within 30 years of its start.", code="never_occurs"
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
        empty rule yields the single ``dtstart`` when it falls in the window. The
        series is walked from ``dtstart``: past :data:`MAX_OCCURRENCES` occurrences
        before ``window_end`` it is refused (``series_too_long``), never truncated.
        """

        require_aware(dtstart=dtstart, window_start=window_start, window_end=window_end)
        tz = get_zone_info(zone)
        after = window_start.astimezone(tz)
        before = window_end.astimezone(tz)
        anchor = dtstart.astimezone(tz)
        if self.never_occurs():
            return []
        if self.rule:
            candidates = list(_from_start(self._compile(anchor), before))
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
        elif self.never_occurs():
            following = None
        else:
            following = self._compile(anchor).after(moment.astimezone(tz), inc=False)
        return following.astimezone(UTC) if following is not None else None

    @_in_range
    def occurrences_after(
        self,
        dtstart: datetime,
        after: datetime,
        until: datetime,
        *,
        zone: str = "",
        limit: int | None = None,
    ) -> list[datetime]:
        """Return the distinct occurrences in ``(after, until]`` as aware UTC, oldest first.

        The series unrolls exactly as :meth:`occurrences` expands it. Without a
        ``limit`` a window holding more than :data:`MAX_OCCURRENCES` raises
        :class:`~django.core.exceptions.ValidationError` (code ``too_many_occurrences``)
        rather than truncating; a caller that pages passes ``limit`` (at most
        :data:`MAX_OCCURRENCES`) and resumes after the last occurrence returned. Two
        wall times a DST change maps to one instant count once.
        """

        require_aware(dtstart=dtstart, after=after, until=until)
        cap = MAX_OCCURRENCES if limit is None else limit
        if not 0 <= cap <= MAX_OCCURRENCES:
            raise ValueError(f"An occurrence limit must be between 0 and {MAX_OCCURRENCES}.")
        if not cap or until <= after:
            return []
        tz = get_zone_info(zone)
        anchor = dtstart.astimezone(tz)
        if not self.rule:
            moments = [anchor] if after < anchor <= until else []
        elif self.never_occurs():
            moments = []
        else:
            end = until.astimezone(tz)
            moments = []
            for moment in self._compile(anchor).xafter(after.astimezone(tz), count=cap + 1, inc=False):
                if moment > end:
                    break
                moments.append(moment)
        found = list(dict.fromkeys(moment.astimezone(UTC) for moment in moments))
        if len(found) > cap:
            if limit is None:
                raise ValidationError(
                    f"The recurrence produces more than {MAX_OCCURRENCES} occurrences in this window.",
                    code="too_many_occurrences",
                )
            found = found[:cap]
        return found

    def never_occurs(self, dtstart: datetime | None = None, *, zone: str = "") -> bool:
        """Whether a non-blank rule yields nothing: never, or, given ``dtstart``, not within 30 years of it.

        Both probes take bounded time. Its day filters alone are probed once over a
        Gregorian cycle (cached). Given ``dtstart``, the rule is probed without its
        ``UNTIL`` and ``COUNT`` from ``dtstart``'s wall clock in ``zone``, moved by
        whole 400-year cycles into the last cycle before dateutil's year limit: the
        calendar repeats, so the first occurrence lands the same distance after it,
        and dateutil scans at most one cycle and the horizon. Once an occurrence is
        known to come within :data:`NEVER_OCCURS_HORIZON`, the rule's own end is
        read from ``dtstart``, a scan that stops there.
        """

        if not self.rule:
            return False
        if _matches_no_day(self.rule):
            return True
        if dtstart is None:
            return False
        require_aware(dtstart=dtstart)
        tz = get_zone_info(zone)
        anchor = dtstart.astimezone(tz)
        local = anchor.replace(tzinfo=None)
        latest_year = (datetime.max - NEVER_OCCURS_HORIZON).year - 1
        cycles = max(0, (latest_year - local.year) // _GREGORIAN_CYCLE_YEARS)
        probe = local.replace(year=local.year + cycles * _GREGORIAN_CYCLE_YEARS)
        first = rrulestr(_open_ended(self.rule), dtstart=probe).after(probe, inc=True)
        if first is None or first - probe > NEVER_OCCURS_HORIZON:
            return True
        return self._compile(anchor).after(anchor, inc=True) is None

    @_in_range
    def get_last_occurrence(self, dtstart: datetime, *, zone: str = "") -> datetime | None:
        """Return the series' last occurrence as aware UTC, or ``None`` when the rule never ends.

        A rule ends when every rule it parses to carries ``UNTIL`` or ``COUNT``; a
        set with one rule that has neither repeats for ever. An empty rule is the
        single ``dtstart``, and a rule that never occurs (:meth:`never_occurs`) has
        no last occurrence. The series unrolls in ``zone`` (blank: the project
        ``TIME_ZONE``), as :meth:`occurrences` expands it. A series of more than
        :data:`MAX_OCCURRENCES` occurrences is refused (``series_too_long``).
        """

        require_aware(dtstart=dtstart)
        tz = get_zone_info(zone)
        anchor = dtstart.astimezone(tz)
        if not self.rule:
            return anchor.astimezone(UTC)
        if self.never_occurs():
            return None
        compiled = self._compile(anchor)
        if not all(rule._count is not None or rule._until is not None for rule in _rules(compiled)):
            return None
        series = list(_from_start(compiled))
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
