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
within about 30 years of it (``never_occurs``), probed in bounded time.

**A read follows its window (re-anchoring, like libical's
``icalrecur_iterator_set_start``).** A rule without ``COUNT`` is read from the rule
period just before the window, not from ``DTSTART``: the parts ``DTSTART`` implies
(the weekday of a weekly rule, the day of a monthly or yearly one, its time of
day) are made explicit first, so the occurrence set is the same. ``UNTIL`` still
ends it. :meth:`Recurrence.occurrences`, :meth:`Recurrence.occurrences_after` and
:meth:`Recurrence.get_occurrence_after` read this way, so a series' age costs
nothing, and :meth:`Recurrence.get_last_occurrence` reads back from ``UNTIL``, so
its length costs nothing either. A ``COUNT`` rule is read from ``DTSTART``: its
count is the cap's. dateutil scans a rule's gap between two occurrences
internally, so a read also pays the gap to the first occurrence past its window:
for a sparse rule, at most one Gregorian cycle of its periods.

One cap, :data:`MAX_OCCURRENCES`, bounds every walk and every ``COUNT``: a window
holding more, or spanning more of the rule's periods from where its walk starts,
is refused (``series_too_long``), never truncated, and so is a ``COUNT`` series
going on past its reach. At save, ``validate(through=…)`` counts a series from
``DTSTART`` to a horizon against the same cap. :meth:`Recurrence.occurrences_after`
refuses more unless the caller pages with ``limit``. Moments are compared as
instants, so a fall-back hour's two passes stay apart. A rule dateutil refuses at
a start is ``invalid``; an expansion that reaches past the representable dates is
refused (``out_of_range``), never overflowed.

Parsing and expansion are delegated to ``python-dateutil`` (``rrulestr``), the
stack's owner of RFC-5545. The timezone/window contract is documented on
:meth:`Recurrence.occurrences` and on ``SchedulingConfig``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from functools import lru_cache, wraps
from itertools import takewhile
from typing import Any, cast

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


@lru_cache(maxsize=1024)
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
    return _parse(probe, _CYCLE_ANCHOR).after(_CYCLE_ANCHOR, inc=True) is None


def _rules(compiled: rrule | rruleset) -> list[rrule]:
    """The rules a parsed recurrence holds: itself, or every rule of a set (dateutil keeps them in ``_rrule``)."""

    return list(compiled._rrule) if isinstance(compiled, rruleset) else [compiled]


def _invalid_rule() -> ValidationError:
    return ValidationError("Enter a valid RFC-5545 recurrence rule (RRULE).", code="invalid")


def _parse(rule: str, dtstart: datetime) -> rrule | rruleset:
    """Parse ``rule`` anchored at ``dtstart`` through dateutil; a rule it refuses there is ``invalid``.

    A rule can parse at one start and not at another (dateutil refuses a
    sub-daily ``BYHOUR`` its ``INTERVAL`` never reaches from that start's hour), so
    every parse, at any start, refuses the same way.
    """

    try:
        return rrulestr(rule, dtstart=dtstart)
    except (ValueError, TypeError, OverflowError) as exc:
        raise _invalid_rule() from exc


def _in_range[**P, R](method: Callable[P, R]) -> Callable[P, R]:
    """Refuse an expansion that reaches past the representable dates as a ``ValidationError``, never a raw error.

    A moment near year 1 or 9999 can leave Python's range once moved into a zone
    (``OverflowError``), and dateutil builds the dates around a period it steps (a
    week-numbered year's neighbours), which can fall before year 1 or past 9999
    (``ValueError``). A rule that does not parse is refused before any expansion
    (:func:`_parse`), so a ``ValueError`` here is a date out of range. The refusal is
    that one row's: a save (a form, a sync's record) refuses it, and a read over
    many rows skips it (``CalendarEventQuerySet.moments``) rather than fail whatever
    it was part of.
    """

    @wraps(method)
    def bounded(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return method(*args, **kwargs)
        except (OverflowError, ValueError) as exc:
            raise ValidationError(
                "This recurrence reaches past the dates it can represent (years 1 to 9999).", code="out_of_range"
            ) from exc

    return bounded


_LONGEST_PERIOD = (
    timedelta(days=366),
    timedelta(days=31),
    timedelta(weeks=1),
    timedelta(days=1),
    timedelta(hours=1),
    timedelta(minutes=1),
    timedelta(seconds=1),
)
"""The longest span of one period of each frequency, in :attr:`Recurrence.FREQUENCIES` order."""


def _series_too_long(*, window: bool) -> ValidationError:
    """The cap's refusal: of a window read from the period before it, or of a series walked from its start."""

    if window:
        message = (
            f"This window holds more than {MAX_OCCURRENCES} occurrences of the recurrence, "
            f"or spans more than {MAX_OCCURRENCES} of its periods."
        )
    else:
        message = (
            f"This recurrence passes {MAX_OCCURRENCES} occurrences, "
            f"or {MAX_OCCURRENCES} of its periods, from its start."
        )
    return ValidationError(message, code="series_too_long")


def moment_after(moment: datetime, span: timedelta) -> datetime | None:
    """Return ``moment + span`` while it stays representable, else ``None``: the span reaches past every date.

    The one range-safe addition of calendar arithmetic. A caller reads ``None`` as
    "no bound in range" (a series' reach) or clamps it to its own last instant (a
    horizon); it never sees an ``OverflowError``.
    """

    try:
        return moment + span
    except OverflowError:
        return None


def _span(compiled: rrule | rruleset, periods: int) -> timedelta | None:
    """Return how long ``periods`` of the rule's shortest period last at most; ``None`` past every span.

    A span too long to represent (``YEARLY;INTERVAL=300``, ten thousand times)
    reaches past every date.
    """

    try:
        return min(_LONGEST_PERIOD[rule._freq] * rule._interval for rule in _rules(compiled)) * periods
    except OverflowError:
        return None


def _reach(compiled: rrule | rruleset, anchor: datetime) -> datetime | None:
    """Return how far :data:`MAX_OCCURRENCES` of the rule's periods reach from ``anchor``; ``None`` past the range.

    A sparse rule (a February 29 filter on a daily rule) costs dateutil one step a
    period whether or not it matches, so a walk stopped here bounds the steps up to
    its last occurrence as well as the occurrences.
    """

    span = _span(compiled, MAX_OCCURRENCES)
    return moment_after(anchor, span) if span is not None else None


def _walk(moments: Iterable[datetime], before: datetime | None, *, window: bool) -> tuple[list[datetime], bool]:
    """Collect a walk's occurrences until the instant ``before``, counting each against the cap.

    Returns them and whether the series goes on at or past ``before``. Past
    :data:`MAX_OCCURRENCES` occurrences the walk refuses (``series_too_long``): the
    ``window`` it reads from the period before it holds too many, or the series it
    walks from its start does.
    """

    collected: list[datetime] = []
    for moment in moments:
        if before is not None and moment >= before:
            return collected, True
        if len(collected) >= MAX_OCCURRENCES:
            raise _series_too_long(window=window)
        collected.append(moment)
    return collected, False


_IMPLIED_PARTS = ("bymonth", "bymonthday", "byweekday", "byhour", "byminute", "bysecond")
"""The rule parts dateutil derives from ``DTSTART`` when a rule leaves them out."""


def _period_start(compiled: rrule, anchor: datetime, target: datetime) -> datetime | None:
    """Return the start of the rule period just before the one holding ``target``, in ``anchor``'s wall clock.

    Periods are ``FREQ`` × ``INTERVAL`` counted from ``DTSTART`` (``anchor``), as
    dateutil steps them: months (twelve a year), days (seven a week, from the week
    start, ``WKST``, that holds ``DTSTART``), or the hours, minutes or seconds of a
    finer rule. The period before is read too, so an occurrence a period reaches
    past its own end (an ISO week of a yearly ``BYWEEKNO``) is never lost. The
    periods are counted first, and ``None`` returned when that period is not past
    the one holding ``DTSTART``: no date before it (none before year 1) is built.
    """

    start = anchor.replace(tzinfo=None)
    moment = target.astimezone(anchor.tzinfo).replace(tzinfo=None)
    freq, step = compiled._freq, compiled._interval
    if freq <= 1:
        months = 12 * step if freq == 0 else step
        origin = start.year * 12 + (start.month - 1 if freq == 1 else 0)
        periods = (moment.year * 12 + moment.month - 1 - origin) // months - 1
        if periods < 1:
            return None
        year, month = divmod(origin + periods * months, 12)
        return datetime(year, month + 1, 1)
    if freq <= 3:
        days = 7 * step if freq == 2 else step
        origin = start.toordinal() - ((start.weekday() - compiled._wkst) % 7 if freq == 2 else 0)
        periods = (moment.toordinal() - origin) // days - 1
        return datetime.fromordinal(origin + periods * days) if periods >= 1 else None
    unit = _LONGEST_PERIOD[freq] * step
    whole = start.replace(
        minute=0 if freq == 4 else start.minute, second=start.second if freq == 6 else 0, microsecond=0
    )
    periods = (moment - whole) // unit - 1
    return whole + unit * periods if periods >= 1 else None


def _reanchored(compiled: rrule | rruleset, anchor: datetime, target: datetime | None) -> tuple[Any, datetime]:
    """Return the rule restarted at the period just before ``target``, and the moment it starts from.

    The re-anchor contract (like libical's ``icalrecur_iterator_set_start``): a
    single rule without ``COUNT`` keeps its occurrence set from any period start,
    once the parts ``DTSTART`` implied are explicit; ``UNTIL`` still ends it. A
    ``COUNT`` rule, a rule set, or a ``target`` not past the second period reads
    from ``DTSTART`` unchanged.
    """

    if target is None or not isinstance(compiled, rrule) or compiled._count is not None:
        return compiled, anchor
    period = _period_start(compiled, anchor, target)
    if period is None:
        return compiled, anchor
    start = period.replace(tzinfo=anchor.tzinfo)
    implied = {
        name: tuple(sorted(resolved)) if isinstance(resolved, set) else resolved
        for name in _IMPLIED_PARTS
        if compiled._original_rule.get(name) is None and (resolved := getattr(compiled, f"_{name}"))
    }
    return compiled.replace(dtstart=start, **implied), start


def _series(
    compiled: rrule | rruleset, anchor: datetime, before: datetime | None, *, after: datetime | None = None
) -> list[datetime]:
    """Return a series' occurrences before the instant ``before`` (``None``: all of it), within its bounds.

    The one walk :meth:`Recurrence.occurrences`, :meth:`Recurrence.get_last_occurrence`
    and ``validate(through=…)`` share. Given ``after`` (a window's start), a rule
    without ``COUNT`` is read from the period just before it (:func:`_reanchored`);
    otherwise from ``DTSTART``. A series whose every rule ends ``UNTIL`` is read no
    further than that end. The walk stops at the reach from where it starts
    (:func:`_reach`), and the series is refused (``series_too_long``) only when it
    goes on past it, so a bounded series read across its own end never is.
    """

    compiled, start = _reanchored(compiled, anchor, after)
    window = start != anchor
    untils = [rule._until for rule in _rules(compiled)]
    if all(until is not None for until in untils):
        end = moment_after(max(cast(list[datetime], untils)), timedelta(microseconds=1))
        if end is not None:
            before = end if before is None else min(before, end)
    reach = _reach(compiled, start)
    if reach is not None and (before is None or before > reach):
        moments, goes_on = _walk(compiled, reach, window=window)
        if goes_on:
            raise _series_too_long(window=window)
        return moments
    return _walk(compiled, before, window=window)[0]


_LOOKBACK = (1, 64, MAX_OCCURRENCES)
"""How many rule periods before its ``UNTIL`` a series' last occurrence is looked for, in turn."""


def _last(compiled: rrule | rruleset, anchor: datetime) -> datetime | None:
    """Return a bounded series' last occurrence (a series whose every rule ends), or ``None`` when it has none.

    A single rule without ``COUNT`` is read back from its ``UNTIL``: re-anchored a
    period before it, then more (:data:`_LOOKBACK`), so its length costs nothing; one
    whose last occurrence lies further back still is walked from ``DTSTART``. A
    ``COUNT`` rule or a rule set is walked from ``DTSTART`` (:func:`_series`).
    """

    if isinstance(compiled, rrule) and compiled._count is None:
        until = cast(datetime, compiled._until)
        for periods in _LOOKBACK:
            span = _span(compiled, periods)
            target = moment_after(until, -span) if span is not None else None
            if target is None:
                break
            rule, start = _reanchored(compiled, anchor, target)
            last = rule.before(until, inc=True)
            if last is not None or start == anchor:
                return last
    series = _series(compiled, anchor, None)
    return series[-1] if series else None


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

    def validate(
        self,
        *,
        finest: str | None = None,
        dtstart: datetime | None = None,
        zone: str = "",
        through: datetime | None = None,
    ) -> None:
        """Raise :class:`~django.core.exceptions.ValidationError` unless the rule parses and stays bounded.

        A blank rule is valid (non-recurring). Otherwise the rule is parsed for its
        grammar alone, anchored at a fixed date and the result discarded — validity
        is ``dtstart``-agnostic, so no start date is required to reject a malformed
        rule. Only one bare ``RRULE`` value is a rule (the field's contract): a rule
        set, or ``DTSTART``, ``EXDATE`` or ``RDATE`` lines, are refused. ``finest``
        (one of :attr:`FREQUENCIES`) refuses rules that repeat more often, and a
        ``COUNT`` over :data:`MAX_OCCURRENCES` is refused. Given the series'
        ``dtstart`` (expanded in ``zone``), a rule with no occurrence within
        :data:`NEVER_OCCURS_HORIZON` of it is refused too (``never_occurs``), and,
        given ``through``, one with more than :data:`MAX_OCCURRENCES` occurrences from
        ``dtstart`` before it (``series_too_long``): the save-time cap a series is
        held to, counted from its start whatever window a later read asks for. A rule
        dateutil refuses at ``dtstart`` (a sub-daily ``BYHOUR`` its ``INTERVAL`` never
        reaches from that hour) is ``invalid`` too.
        """

        if not self.rule:
            return
        if any(mark in self.rule for mark in ("\n", "\r", ":")):
            raise ValidationError(
                "Enter one bare recurrence rule (FREQ=…), without DTSTART, EXDATE or RDATE lines.", code="invalid"
            )
        rules = _rules(self._compile(_VALIDATION_ANCHOR))
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
        if dtstart is not None and through is not None:
            self._count_through(dtstart, through, zone=zone)

    @_in_range
    def _count_through(self, dtstart: datetime, through: datetime, *, zone: str) -> None:
        """Refuse a series with more than :data:`MAX_OCCURRENCES` occurrences from ``dtstart`` before ``through``."""

        anchor = dtstart.astimezone(get_zone_info(zone))
        _series(self._compile(anchor), anchor, through.astimezone(UTC))

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
        series is read from the period just before ``window_start`` (a rule without
        ``COUNT``) or from ``dtstart`` (:func:`_series`): past :data:`MAX_OCCURRENCES`
        occurrences before ``window_end``, or going on past its reach, it is refused
        (``series_too_long``), never truncated. The window holds instants: the two
        passes of a fall-back hour stay apart, and two wall times a DST change maps
        to one instant (the spring gap) count once.
        """

        require_aware(dtstart=dtstart, window_start=window_start, window_end=window_end)
        anchor = dtstart.astimezone(get_zone_info(zone))
        first, last = window_start.astimezone(UTC), window_end.astimezone(UTC)
        if self.never_occurs():
            return []
        candidates = _series(self._compile(anchor), anchor, last, after=first) if self.rule else [anchor]
        found = (moment.astimezone(UTC) for moment in candidates)
        return list(dict.fromkeys(moment for moment in found if first <= moment < last))

    @_in_range
    def get_occurrence_after(self, dtstart: datetime, moment: datetime, *, zone: str = "") -> datetime | None:
        """Return the first occurrence strictly after ``moment`` as aware UTC, or ``None`` once the rule ends.

        The series starts at ``dtstart`` and unrolls in ``zone`` (blank: the project
        ``TIME_ZONE``), exactly as :meth:`occurrences` expands it; ``moment`` need
        not be an occurrence. An empty rule is the single ``dtstart``. This is
        dateutil's native ``rrule.after``, read from the period just before
        ``moment`` (a rule without ``COUNT``), so a rule with no ``UNTIL`` or
        ``COUNT`` never ends.
        """

        require_aware(dtstart=dtstart, moment=moment)
        tz = get_zone_info(zone)
        anchor = dtstart.astimezone(tz)
        if not self.rule:
            following: datetime | None = anchor if anchor > moment else None
        elif self.never_occurs():
            following = None
        else:
            instant = moment.astimezone(UTC)
            compiled, _start = _reanchored(self._compile(anchor), anchor, instant)
            following = compiled.after(instant, inc=False)
        return following.astimezone(UTC) if following is not None else None

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
        found = self._following(dtstart, after.astimezone(UTC), until.astimezone(UTC), zone=zone, count=cap + 1)
        if len(found) > cap:
            if limit is None:
                raise ValidationError(
                    f"The recurrence produces more than {MAX_OCCURRENCES} occurrences in this window.",
                    code="too_many_occurrences",
                )
            found = found[:cap]
        return found

    @_in_range
    def _following(
        self, dtstart: datetime, first: datetime, last: datetime, *, zone: str, count: int
    ) -> list[datetime]:
        """Return at most ``count`` distinct occurrences in the instants ``(first, last]``, as aware UTC."""

        anchor = dtstart.astimezone(get_zone_info(zone))
        if not self.rule:
            moments = [anchor] if first < anchor <= last else []
        elif self.never_occurs():
            moments = []
        else:
            compiled, _start = _reanchored(self._compile(anchor), anchor, first)
            moments = list(takewhile(lambda moment: moment <= last, compiled.xafter(first, count=count, inc=False)))
        return list(dict.fromkeys(moment.astimezone(UTC) for moment in moments))

    @_in_range
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
        first = _parse(_open_ended(self.rule), probe).after(probe, inc=True)
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
        ``TIME_ZONE``), as :meth:`occurrences` expands it. A rule that ends ``UNTIL`` is
        read back from it (re-anchored), so its length costs nothing; a ``COUNT``
        rule is walked from ``dtstart`` (its count is the cap's), as is one whose
        last occurrence lies more than :data:`MAX_OCCURRENCES` of its periods before
        its ``UNTIL``, refused past them (``series_too_long``).
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
        last = _last(compiled, anchor)
        return last.astimezone(UTC) if last is not None else None

    def _compile(self, anchor: datetime) -> rrule | rruleset:
        """Parse the rule anchored at an aware local ``anchor`` (a dateutil rrule/rruleset).

        A floating ``UNTIL`` past the representable UTC instants ends with their
        range: at the last one (9999-12-31 in a zone behind UTC), or the first (year
        1 in a zone ahead of it). A rule dateutil refuses here is ``invalid``
        (:func:`_parse`).
        """

        def local_until(match: re.Match[str]) -> str:
            # Dateutil remains the date grammar owner; adapt only the legacy
            # floating cutoff to the aware DTSTART contract it requires.
            until = parse(match[1], default=anchor.replace(tzinfo=None, hour=0, minute=0, second=0))
            if until.tzinfo is None:
                try:
                    bound = until.replace(tzinfo=anchor.tzinfo).astimezone(UTC)
                except OverflowError:
                    bound = (datetime.min if until.year == 1 else datetime.max).replace(tzinfo=UTC)
                # isoformat pads every year to four digits; a platform's strftime need not.
                stamp = bound.replace(tzinfo=None).isoformat(timespec="seconds")
                return "UNTIL=" + stamp.replace("-", "").replace(":", "") + "Z"
            return match[0]

        try:
            rule = re.sub(r"(?im)(?<![A-Z])UNTIL=([^;\s]+)", local_until, self.rule)
        except (ValueError, OverflowError) as exc:
            raise _invalid_rule() from exc
        return _parse(rule, anchor)
