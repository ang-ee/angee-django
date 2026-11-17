"""Due rules: when a record is due, measured from named anchors, as a value.

The addon README lists what a rule can express and the consumer recipes; each class
below owns its exact contract.

A :class:`DueRule` is stored on a consumer row in a
:class:`~angee.scheduling.fields.DueRuleField`. Its :class:`Term`\\ s are measured
from **due anchors**, named points the subject's owner supplies to
:meth:`DueRule.resolve`, which is pure (no database, no clock) and returns a
:class:`Due`. The consumer writes ``Due.at`` and/or ``Due.local_date`` into its own
column and ``Due.as_json()`` into a basis column; :meth:`Due.get_status` is computed
from that basis when read and is never stored.

**Time contract.** Moments are aware; a naive one is refused. Each term runs in one
zone: its own ``zone``, else its anchor's, else the project ``TIME_ZONE``. Minutes
and hours are elapsed time on the UTC timeline; days to years and every alignment
are wall-calendar steps (``dateutil.relativedelta``) in that zone, and
``local_date`` is the point's date there. A term over an instant resolves to an
instant; a term over a local day stays a day until a sub-day offset or
:meth:`Align.to_time` makes it an instant, and :meth:`Align.to_local_day` turns an
instant back into its day.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Mapping
from datetime import UTC
from decimal import Decimal
from typing import Annotated, Any, Literal, Self, cast

from dateutil.relativedelta import FR, MO, SA, SU, TH, TU, WE, relativedelta
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    field_serializer,
    model_validator,
)

from angee.base.validators import validate_value
from angee.scheduling.recurrence import Recurrence
from angee.scheduling.zones import ZoneName, get_zone_info, require_aware

type CalendarUnit = Literal["minutes", "hours", "days", "weeks", "months", "years"]
type Period = Literal["day", "week", "month", "quarter", "year"]
type Weekday = Literal["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
type Combine = Literal["earliest", "latest"]
type AnchorValue = Anchor | dt.datetime | dt.date | None
"""What a subject supplies per anchor name: an :class:`Anchor`, a bare instant or day, or ``None`` (not yet known)."""

type _Point = dt.date | dt.datetime


def _decimal_json(value: Decimal) -> str:
    """Spell a decimal one way: fixed-point, without trailing zeros or a negative zero."""

    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


_Decimal = Annotated[Decimal, Field(allow_inf_nan=False), PlainSerializer(_decimal_json, when_used="json")]
_Instant = Annotated[AwareDatetime, AfterValidator(lambda moment: moment.astimezone(UTC))]

_CALENDAR_LIMITS = {
    "minutes": 525_960_000,
    "hours": 8_766_000,
    "days": 365_250,
    "weeks": 52_179,
    "months": 12_000,
    "years": 1_000,
}
"""The furthest one offset reaches in each calendar unit: a thousand years."""

_COUNTER_LIMIT = Decimal(10) ** 12
_ELAPSED_STEPS: dict[str, Callable[[int], dt.timedelta]] = {
    "minutes": lambda amount: dt.timedelta(minutes=amount),
    "hours": lambda amount: dt.timedelta(hours=amount),
}
_CALENDAR_STEPS: dict[str, Callable[[int], relativedelta]] = {
    "days": lambda amount: relativedelta(days=amount),
    "weeks": lambda amount: relativedelta(weeks=amount),
    "months": lambda amount: relativedelta(months=amount),
    "years": lambda amount: relativedelta(years=amount),
}
_WEEKDAYS = {"MO": MO, "TU": TU, "WE": WE, "TH": TH, "FR": FR, "SA": SA, "SU": SU}
_MICROSECONDS_PER_DAY = Decimal(86_400_000_000)


class DueStatus(models.TextChoices):
    """Where ``now`` (or a counter's reading) stands against a due point, in rising severity."""

    OK = "ok", "OK"
    WARNING = "warning", "Warning"
    DUE = "due", "Due"
    GRACE = "grace", "Grace"
    OVERDUE = "overdue", "Overdue"


_SEVERITY = tuple(DueStatus)


class _Value(BaseModel):
    """An immutable due-rule value with one canonical JSON spelling."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    def as_json(self) -> dict[str, Any]:
        """Return the value as canonical JSON: what a column stores.

        Defaults are left out, so every default is part of the stored contract:
        changing one would change what each stored value means. Calendar amounts
        are integers; other decimals are fixed-point strings without trailing zeros;
        instants are UTC.
        """

        return self.model_dump(mode="json", exclude_defaults=True)

    @classmethod
    def from_json(cls, value: Any) -> Self | None:
        """Read a stored value back: ``None`` for a blank column (``NULL`` or ``{}``).

        Anything else that does not validate raises Django's
        :class:`~django.core.exceptions.ValidationError`, with paths under the class
        name.
        """

        if value is None or value == {}:
            return None
        return cast(Self, validate_value(cls, value, field=cls.__name__))


def _in_zone(moment: dt.datetime, zone: dt.tzinfo) -> dt.datetime:
    """Return ``moment`` as a real instant in ``zone``; a wall time a DST gap skips moves past the gap."""

    return moment.astimezone(UTC).astimezone(zone)


def _day_edge(day: dt.date, zone: dt.tzinfo, edge: Literal["start", "end"]) -> dt.datetime:
    """Return the first or last instant of a local day."""

    return _in_zone(dt.datetime.combine(day, dt.time.min if edge == "start" else dt.time.max, zone), zone)


def _period(day: dt.date, period: Period) -> tuple[dt.date, dt.date]:
    """Return the first and last day of the period containing ``day``; weeks start on Monday (ISO)."""

    match period:
        case "day":
            return day, day
        case "week":
            monday = day - dt.timedelta(days=day.weekday())
            return monday, monday + dt.timedelta(days=6)
        case "month":
            return day.replace(day=1), day + relativedelta(day=31)
        case "quarter":
            first = dt.date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)
            return first, first + relativedelta(months=2, day=31)
        case "year":
            return dt.date(day.year, 1, 1), dt.date(day.year, 12, 31)


class Offset(_Value):
    """A signed amount: a calendar step when it names a unit, otherwise an amount in a counter's own unit.

    ``Offset(-48, "hours")`` is 48 elapsed hours earlier, across a DST change;
    ``Offset(7, "days")`` keeps the wall-clock time seven calendar days later, and a
    month from 31 January is the last day of February (``relativedelta``). Calendar
    amounts count whole units and reach at most a thousand years; a counter amount
    (``Offset(300)``) at most 10**12 of its unit.
    """

    amount: Annotated[Decimal, Field(allow_inf_nan=False)]
    unit: CalendarUnit | None = None

    def __init__(self, amount: Decimal | int | str, unit: CalendarUnit | None = None, **data: Any) -> None:
        """Accept the amount and unit positionally, as a rule is read aloud."""

        super().__init__(**{"amount": amount, "unit": unit, **data})

    @model_validator(mode="after")
    def _fits_its_unit(self) -> Self:
        limit = _COUNTER_LIMIT if self.unit is None else _CALENDAR_LIMITS[self.unit]
        if abs(self.amount) > limit:
            raise ValueError(f"An offset reaches at most {limit} {self.unit or 'counter units'}.")
        if self.unit is not None and self.amount != self.amount.to_integral_value():
            raise ValueError("A calendar offset counts whole units.")
        return self

    @field_serializer("amount", when_used="json")
    def _amount_json(self, amount: Decimal) -> int | str:
        return int(amount) if self.unit is not None else _decimal_json(amount)

    def _apply(self, point: _Point, zone: dt.tzinfo, *, sign: int = 1) -> _Point:
        """Step a calendar point by this offset times ``sign``; elapsed units start a local day at its first instant."""

        assert self.unit is not None  # a counter amount never steps a calendar point
        amount = sign * int(self.amount)
        if self.unit in _ELAPSED_STEPS:
            moment = point if isinstance(point, dt.datetime) else _day_edge(point, zone, "start")
            return (moment.astimezone(UTC) + _ELAPSED_STEPS[self.unit](amount)).astimezone(zone)
        stepped = point + _CALENDAR_STEPS[self.unit](amount)
        return _in_zone(stepped, zone) if isinstance(stepped, dt.datetime) else stepped


class Align(_Value):
    """One alignment step, applied in order after a term's offset in the term's zone.

    - :meth:`to_start_of` / :meth:`to_end_of` a period (day, week, month, quarter,
      year); on an instant they keep the zone's first or last instant of that day.
    - :meth:`to_next_weekday` / :meth:`to_previous_weekday`: on or after (before)
      the point, or strictly after (before) it with ``strict=True``.
    - :meth:`to_time`: a local clock time on the point's local date; the point
      becomes an instant.
    - :meth:`to_local_day`: an instant drops to its local date; the point becomes a
      day, due all that day.
    """

    kind: Literal["start_of", "end_of", "next_weekday", "previous_weekday", "at_time", "local_day"]
    period: Period | None = None
    weekday: Weekday | None = None
    strict: bool = False
    time: dt.time | None = None

    @model_validator(mode="after")
    def _fields_fit_the_kind(self) -> Self:
        needs = {"start_of": "period", "end_of": "period", "at_time": "time", "local_day": ""}.get(self.kind, "weekday")
        given = {name for name in ("period", "weekday", "time") if getattr(self, name) is not None}
        if given != ({needs} if needs else set()):
            raise ValueError(f"{self.kind} takes {needs or 'no target'} and nothing else.")
        if self.strict and needs != "weekday":
            raise ValueError("strict applies to a weekday alignment.")
        return self

    @classmethod
    def to_start_of(cls, period: Period) -> Align:
        """Align to the first day (first instant) of the period containing the point."""

        return cls(kind="start_of", period=period)

    @classmethod
    def to_end_of(cls, period: Period) -> Align:
        """Align to the last day (last instant) of the period containing the point."""

        return cls(kind="end_of", period=period)

    @classmethod
    def to_next_weekday(cls, weekday: Weekday, *, strict: bool = False) -> Align:
        """Align to the weekday on or after the point, or strictly after it."""

        return cls(kind="next_weekday", weekday=weekday, strict=strict)

    @classmethod
    def to_previous_weekday(cls, weekday: Weekday, *, strict: bool = False) -> Align:
        """Align to the weekday on or before the point, or strictly before it."""

        return cls(kind="previous_weekday", weekday=weekday, strict=strict)

    @classmethod
    def to_time(cls, time: dt.time | str) -> Align:
        """Align to a local clock time (``"14:00"``) on the point's local date."""

        return cls.model_validate({"kind": "at_time", "time": time})

    @classmethod
    def to_local_day(cls) -> Align:
        """Align an instant to its local date, so the point is due all that day."""

        return cls(kind="local_day")

    def _apply(self, point: _Point, zone: dt.tzinfo) -> _Point:
        day = point.date() if isinstance(point, dt.datetime) else point
        if self.kind == "local_day":
            return day
        if self.time is not None:
            return _in_zone(dt.datetime.combine(day, self.time, zone), zone)
        if self.period is not None:
            first, last = _period(day, self.period)
            if self.kind == "start_of":
                return _day_edge(first, zone, "start") if isinstance(point, dt.datetime) else first
            return _day_edge(last, zone, "end") if isinstance(point, dt.datetime) else last
        assert self.weekday is not None  # _fields_fit_the_kind admits nothing else
        direction = 1 if self.kind == "next_weekday" else -1
        start = point + relativedelta(days=direction) if self.strict else point
        stepped = start + relativedelta(weekday=_WEEKDAYS[self.weekday](direction))
        return _in_zone(stepped, zone) if isinstance(stepped, dt.datetime) else stepped


class Anchor(_Value):
    """A due anchor's value, supplied by the subject's owner: an instant or a local day.

    ``zone`` names the wall clock the anchor's terms step in unless a term names its
    own (a venue's zone); blank takes the project ``TIME_ZONE``. ``readings``
    snapshot counters at the anchor for counter terms
    (``Anchor(on=…, readings={"usage_hours": Decimal("6357.7")})``). A bare aware
    ``datetime`` or a ``date`` reads as an anchor.
    """

    at: _Instant | None = None
    on: dt.date | None = None
    zone: ZoneName = ""
    readings: dict[str, _Decimal] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _from_a_bare_moment(cls, value: Any) -> Any:
        if isinstance(value, dt.datetime):
            return {"at": value}
        if isinstance(value, dt.date):
            return {"on": value}
        return value

    @model_validator(mode="after")
    def _one_moment(self) -> Self:
        if (self.at is None) == (self.on is None):
            raise ValueError("An anchor is either an instant (at) or a local day (on).")
        return self

    def _point(self, zone: dt.tzinfo) -> _Point:
        if self.at is not None:
            return self.at.astimezone(zone)
        assert self.on is not None  # _one_moment admits an instant or a day
        return self.on


class Counter(_Value):
    """A counter's current state, supplied by its consumer: a reading, when it was taken, and an optional rate.

    ``rate_per_day`` (in the counter's unit) lets a counter term project the instant
    its target is reached; the consumer owns how the rate is measured. A zero rate
    never reaches the target, so it projects nothing; a negative rate is refused.
    """

    reading: _Decimal
    at: _Instant
    rate_per_day: Annotated[_Decimal, Field(ge=0)] | None = None

    def _reaches(self, target: Decimal) -> dt.datetime | None:
        if not self.rate_per_day:
            return None
        days = (target - self.reading) / self.rate_per_day
        try:
            return self.at + dt.timedelta(microseconds=int((days * _MICROSECONDS_PER_DAY).to_integral_value()))
        except OverflowError:
            return None


class Repeat(_Value):
    """What follows once the work a due point set is done (:meth:`DueRule.resolve_next`).

    - ``Repeat()``, the default: nothing; the rule ends once its due point is met.
    - :meth:`from_completion`: the rule resolves again with the completion as its
      ``anchor`` (``last_done`` by default), so the next due counts from the work.
    - :meth:`from_rrule`: the next occurrence of an RRULE after the previous due
      point (after its whole day, for a local day), independent of when the work was
      done. The series restarts at each due point, so a rule ends with ``UNTIL``;
      ``COUNT`` is refused.
    """

    kind: Literal["once", "from_completion", "calendar"] = "once"
    anchor: str = ""
    rule: str = ""

    @model_validator(mode="after")
    def _fields_fit_the_kind(self) -> Self:
        if bool(self.anchor) != (self.kind == "from_completion"):
            raise ValueError("anchor names the completion anchor of a from_completion repeat, and only that.")
        if bool(self.rule) != (self.kind == "calendar"):
            raise ValueError("rule is the RRULE of a calendar repeat, and only that.")
        if self.rule:
            try:
                Recurrence(self.rule).validate()
            except ValidationError as error:
                raise ValueError(error.messages[0]) from error
            if "COUNT=" in self.rule.upper():
                raise ValueError("A calendar repeat restarts at each due point; end it with UNTIL, not COUNT.")
        return self

    @classmethod
    def from_completion(cls, anchor: str = "last_done") -> Repeat:
        """Repeat from the completion, supplied as ``anchor``."""

        return cls(kind="from_completion", anchor=anchor)

    @classmethod
    def from_rrule(cls, rule: str) -> Repeat:
        """Repeat on an RRULE's next occurrence after the previous due point."""

        return cls(kind="calendar", rule=rule)


class Term(_Value):
    """One limit measured from a due anchor: a calendar term, or a counter term when ``counter`` is named.

    A calendar term steps the anchor by ``offset`` (a calendar unit), then applies
    ``align`` in order, all in ``zone`` (blank: the anchor's zone, else the project
    ``TIME_ZONE``). A counter term's target is the anchor's reading of ``counter``
    plus ``offset`` (an amount in the counter's unit). ``warn`` opens the warning
    window before the due point and ``grace`` allows the time or amount after it, in
    the term's own measure. ``label`` names the term in a rule with several and in
    the resolution's ``winner``.
    """

    label: str = ""
    anchor: str = Field(min_length=1)
    counter: str = ""
    zone: ZoneName = ""
    offset: Offset | None = None
    align: tuple[Align, ...] = ()
    warn: Offset | None = None
    grace: Offset | None = None

    @model_validator(mode="after")
    def _amounts_fit_the_measure(self) -> Self:
        windows = [window for window in (self.warn, self.grace) if window is not None]
        if any(window.amount < 0 for window in windows):
            raise ValueError("Warn and grace windows are not negative.")
        amounts = [*windows, *([self.offset] if self.offset is not None else [])]
        if self.counter:
            if any(amount.unit is not None for amount in amounts):
                raise ValueError("A counter term's amounts are in the counter's unit and name no calendar unit.")
            if self.align:
                raise ValueError("A counter term has no calendar alignment.")
        elif any(amount.unit is None for amount in amounts):
            raise ValueError("A calendar term's offsets name a calendar unit.")
        return self


def _outside_the_calendar(term: Term) -> ValidationError:
    return ValidationError(
        {f"anchors.{term.anchor}": ["The term's due point or its windows fall outside the calendar."]}
    )


class TermDue(_Value):
    """One term's resolution inside a :class:`Due`; with the term itself it explains the point.

    ``origin`` is the anchor value the point was stepped from, ``None`` while that
    anchor is pending; for a calendar repeat it is the previous due point and
    ``repeat`` names the RRULE that stepped it. A calendar term carries ``at`` (an
    instant, UTC) and/or ``local_date`` (the point's date in ``zone``); a counter
    term carries ``target`` and, when its counter has a rate, the projected ``at``
    with the ``forecast`` it came from. ``zone`` is the zone the term ran in, so
    status is read in the same zone later.
    """

    term: Term
    zone: ZoneName = ""
    origin: Anchor | None = None
    repeat: Repeat | None = None
    at: _Instant | None = None
    local_date: dt.date | None = None
    target: _Decimal | None = None
    forecast: Counter | None = None

    @classmethod
    def _resolve(cls, term: Term, origin: Anchor | None, counter: Counter | None) -> TermDue:
        if origin is None:
            return cls(term=term)
        zone_name = term.zone or origin.zone or timezone.get_default_timezone_name()
        zone = get_zone_info(zone_name)
        if term.counter:
            if term.counter not in origin.readings:
                raise ValidationError({f"anchors.{term.anchor}.readings": [f"No {term.counter!r} reading."]})
            target = origin.readings[term.counter] + (term.offset.amount if term.offset is not None else 0)
            at = counter._reaches(target) if counter is not None else None
            try:
                local_date = at.astimezone(zone).date() if at is not None else None
            except OverflowError:  # a forecast past the calendar's end reads as never reached
                at = local_date = None
            return cls(
                term=term,
                zone=zone_name,
                origin=origin,
                target=target,
                at=at,
                local_date=local_date,
                forecast=counter if at is not None else None,
            )
        try:
            point = origin._point(zone)
            if term.offset is not None:
                point = term.offset._apply(point, zone)
            for step in term.align:
                point = step._apply(point, zone)
        except (OverflowError, ValueError) as error:
            raise _outside_the_calendar(term) from error
        if isinstance(point, dt.datetime):
            resolved = cls(term=term, zone=zone_name, origin=origin, at=point, local_date=point.date())
        else:
            resolved = cls(term=term, zone=zone_name, origin=origin, local_date=point)
        try:
            resolved._calendar_bounds()  # the windows must fit the calendar too, so status never fails
        except (OverflowError, ValueError) as error:
            raise _outside_the_calendar(term) from error
        return resolved

    def _span(self) -> tuple[dt.datetime, dt.datetime] | None:
        """The first and last instant the point covers: a local day's whole span, or the instant itself."""

        if self.at is not None:
            return self.at, self.at
        if self.local_date is None:
            return None
        zone = get_zone_info(self.zone)
        return (
            _day_edge(self.local_date, zone, "start").astimezone(UTC),
            _day_edge(self.local_date, zone, "end").astimezone(UTC),
        )

    def _calendar_bounds(self) -> tuple[dt.datetime, dt.datetime, dt.datetime | None, dt.datetime]:
        span = self._span()
        assert span is not None  # a resolved calendar term has its point
        start, end = span
        zone = get_zone_info(self.zone)
        warn, grace = self.term.warn, self.term.grace
        warn_from = cast(dt.datetime, warn._apply(start.astimezone(zone), zone, sign=-1)) if warn else None
        grace_until = cast(dt.datetime, grace._apply(end.astimezone(zone), zone)) if grace else end
        return (
            start,
            end,
            warn_from.astimezone(UTC) if warn_from is not None else None,
            grace_until.astimezone(UTC),
        )

    def _counter_bounds(self) -> tuple[Decimal, Decimal, Decimal | None, Decimal]:
        assert self.target is not None  # a resolved counter term has its target
        warn, grace = self.term.warn, self.term.grace
        return (
            self.target,
            self.target,
            self.target - warn.amount if warn is not None else None,
            self.target + grace.amount if grace is not None else self.target,
        )

    @property
    def warn_from(self) -> dt.datetime | Decimal | None:
        """Where the warning window opens: an aware instant (UTC) for a calendar term, a reading for a counter term.

        ``None`` without ``warn`` or while the anchor is pending. For a local day the
        window reaches back from the day's first instant.
        """

        if self.origin is None:
            return None
        return (self._counter_bounds() if self.term.counter else self._calendar_bounds())[2]

    @property
    def grace_until(self) -> dt.datetime | Decimal | None:
        """The last instant (UTC) or reading before overdue: the end of the due point plus ``grace``.

        Without ``grace`` it is the end of the point itself (a local day's last
        instant); ``None`` while the anchor is pending.
        """

        if self.origin is None:
            return None
        return (self._counter_bounds() if self.term.counter else self._calendar_bounds())[3]

    def _get_status(self, *, now: dt.datetime, counters: Mapping[str, Counter]) -> DueStatus:
        if not self.term.counter:
            return _classify(lambda bound: now >= bound, lambda bound: now > bound, *self._calendar_bounds())
        counter = counters.get(self.term.counter)
        if counter is None:
            raise ValidationError({"counters": [f"Status needs the current {self.term.counter!r} reading."]})
        reading = counter.reading
        return _classify(lambda bound: reading >= bound, lambda bound: reading > bound, *self._counter_bounds())


def _classify[B](
    reached: Callable[[B], bool],
    beyond: Callable[[B], bool],
    due_from: B,
    due_until: B,
    warn_from: B | None,
    grace_until: B,
) -> DueStatus:
    """The one status ladder every measure shares, from the most severe rung down."""

    if beyond(grace_until):
        return DueStatus.OVERDUE
    if beyond(due_until):
        return DueStatus.GRACE
    if reached(due_from):
        return DueStatus.DUE
    if warn_from is not None and reached(warn_from):
        return DueStatus.WARNING
    return DueStatus.OK


class Due(_Value):
    """A rule's resolution: the due point, the counter targets, the winning term, as a basis.

    ``at`` (UTC) and/or ``local_date`` (the date in the winning term's zone) come
    from the winning dated term (calendar terms, and counter terms with a forecast).
    A counter term without a forecast cannot be placed on the calendar: its target
    is in ``counter_targets`` and it counts in :meth:`get_status`.

    ``earliest`` places the point from the resolved terms, which already bound it,
    and lists any ``pending`` anchors: the point can only move earlier. ``latest``
    has no point (and no status) while any anchor is pending, and no point while a
    resolved term is undated. ``Due.as_json()`` is the basis to store and
    ``Due.from_json`` reads it back.
    """

    terms: tuple[TermDue, ...] = Field(min_length=1)
    combine: Combine = "earliest"
    pending: tuple[str, ...] = ()
    winner: str | None = None
    at: _Instant | None = None
    local_date: dt.date | None = None
    counter_targets: dict[str, _Decimal] = Field(default_factory=dict)

    @classmethod
    def _combine(cls, terms: tuple[TermDue, ...], combine: Combine) -> Due:
        pending = tuple(sorted({resolved.term.anchor for resolved in terms if resolved.origin is None}))
        resolved = [(index, term) for index, term in enumerate(terms) if term.origin is not None]
        pick = min if combine == "earliest" else max
        targets: dict[str, Decimal] = {}
        for _, term in resolved:
            if term.target is not None:
                name = term.term.counter
                targets[name] = pick(targets[name], term.target) if name in targets else term.target
        ends = [(span[1], index) for index, term in resolved if (span := term._span()) is not None]
        if not ends or (combine == "latest" and (pending or len(ends) < len(resolved))):
            return cls(terms=terms, combine=combine, pending=pending, counter_targets=targets)
        # Ties keep the first-declared term in both directions.
        _, index = min(ends) if combine == "earliest" else max(ends, key=lambda item: (item[0], -item[1]))
        winner = terms[index]
        return cls(
            terms=terms,
            combine=combine,
            pending=pending,
            winner=winner.term.label,
            at=winner.at,
            local_date=winner.local_date,
            counter_targets=targets,
        )

    def get_status(self, *, now: dt.datetime, counters: Mapping[str, Counter] | None = None) -> DueStatus | None:
        """Return the status at ``now``; computed on read, never stored.

        A local day is due all that day; an instant is due only at that exact moment,
        so an instant term passes from ``warning`` to ``grace`` or ``overdue`` (align
        it with :meth:`Align.to_local_day` to make it due for a day). ``earliest``
        reports the most severe resolved term, even while another anchor is pending;
        ``latest`` the least severe, and ``None`` while any anchor is pending. Counter
        terms read the current ``counters`` reading, never the forecast.
        """

        require_aware(now=now)
        resolved = [term for term in self.terms if term.origin is not None]
        if not resolved or (self.combine == "latest" and self.pending):
            return None
        readings = validate_value(dict[str, Counter], dict(counters or {}), field="counters")
        statuses = [term._get_status(now=now, counters=readings) for term in resolved]
        pick = max if self.combine == "earliest" else min
        return pick(statuses, key=_SEVERITY.index)


class DueRule(_Value):
    """A declarative due rule: labelled terms over named due anchors, combined and repeated.

    ``DueRule(terms=[Term(anchor="first_slot", offset=Offset(-48, "hours"))])`` is due
    48 hours before an anchor the subject learns later. Several terms combine as
    ``earliest`` (whichever comes first) or ``latest`` (whichever comes last), and
    then each needs a distinct ``label``. ``repeat`` says what follows once the work
    is done (:class:`Repeat`).
    """

    terms: tuple[Term, ...]
    combine: Combine = "earliest"
    repeat: Repeat = Repeat()

    @model_validator(mode="after")
    def _terms_fit_the_rule(self) -> Self:
        if not self.terms:
            raise ValueError("A rule has at least one term.")
        labels = [term.label for term in self.terms]
        if len(self.terms) > 1 and (not all(labels) or len(set(labels)) != len(labels)):
            raise ValueError("Each term of a rule with several needs its own label.")
        if self.repeat.kind == "from_completion" and self.repeat.anchor not in {term.anchor for term in self.terms}:
            raise ValueError(f"No term counts from the completion anchor {self.repeat.anchor!r}.")
        if self.repeat.kind == "calendar" and (len(self.terms) != 1 or self.terms[0].counter):
            raise ValueError("A calendar repeat steps a rule of one calendar term.")
        return self

    def resolve(self, *, anchors: Mapping[str, AnchorValue], counters: Mapping[str, Counter] | None = None) -> Due:
        """Resolve the rule against the subject's anchors and its counters' current state; pure.

        A missing or ``None`` anchor leaves its terms pending and names it in
        ``Due.pending``. Invalid anchors or counters, and a point or window that
        falls outside the calendar, raise Django's
        :class:`~django.core.exceptions.ValidationError` with paths under
        ``anchors`` and ``counters``.
        """

        known = validate_value(dict[str, Anchor | None], dict(anchors), field="anchors")
        states = validate_value(dict[str, Counter], dict(counters or {}), field="counters")
        terms = tuple(
            TermDue._resolve(term, known.get(term.anchor), states.get(term.counter) if term.counter else None)
            for term in self.terms
        )
        return Due._combine(terms, self.combine)

    def resolve_next(
        self,
        due: Due,
        *,
        done: AnchorValue,
        anchors: Mapping[str, AnchorValue] | None = None,
        counters: Mapping[str, Counter] | None = None,
    ) -> Due | None:
        """Return the resolution that follows ``due`` once its work is ``done``, or ``None`` when the rule ends.

        ``from_completion`` resolves again with ``done`` as the completion anchor
        (with readings for counter terms) beside the other ``anchors``. ``calendar``
        steps ``due``'s point to the RRULE's first occurrence after it in the term's
        zone (after the whole day for a local day, which stays a day), whatever
        ``done`` says; the stepped term's ``origin`` is the previous point and its
        ``repeat`` the rule.
        """

        match self.repeat.kind:
            case "once":
                return None
            case "from_completion":
                return self.resolve(anchors={**(anchors or {}), self.repeat.anchor: done}, counters=counters)
        if due.local_date is None:
            raise ValidationError({"due": ["A calendar repeat follows a resolved due point."]})
        [resolved] = due.terms
        zone = get_zone_info(resolved.zone)
        if due.at is not None:
            start = after = due.at
            origin = Anchor(at=due.at, zone=resolved.zone)
        else:
            start, after = _day_edge(due.local_date, zone, "start"), _day_edge(due.local_date, zone, "end")
            origin = Anchor(on=due.local_date, zone=resolved.zone)
        following = Recurrence(self.repeat.rule).get_occurrence_after(start, after, zone=resolved.zone)
        if following is None:
            return None
        at = following if due.at is not None else None
        local_date = following.astimezone(zone).date()
        stepped = resolved.model_copy(
            update={"origin": origin, "repeat": self.repeat, "at": at, "local_date": local_date}
        )
        return Due(terms=(stepped,), combine=due.combine, winner=due.winner, at=at, local_date=local_date)
