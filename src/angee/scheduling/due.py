"""Due rules: when a record is due, measured from named anchors, as a value.

A :class:`DueRule` is the declarative value a consumer row stores in a
:class:`~angee.scheduling.fields.DueRuleField`, beside its own date or instant
column. It names **due anchors** (points the subject learns over time, such as
``created``, ``first_slot`` or ``last_done``) and one or more :class:`Term`\\ s
measured from them: a calendar or counter :class:`Offset`, ordered
:class:`Align` steps, and warn and grace windows. Scheduling owns no anchors,
counters or rates. The subject's owner supplies them to :meth:`DueRule.resolve`,
which is pure (no database, no clock) and returns a :class:`Due`.

The consumer writes ``Due.at`` and/or ``Due.local_date`` into its own column and
``Due.basis`` into a JSON column beside it. :meth:`Due.status` is computed from
that basis when read, against ``now`` and the current counter readings, and is
never stored. ``Due.model_validate(row.due_basis)`` reads a stored basis back.

**Time contract** (the scheduling owner of "what day is it"). Moments in and out
are aware; a naive one is refused. ``minutes`` and ``hours`` are elapsed time on
the UTC timeline. ``days``, ``weeks``, ``months`` and ``years`` and every
alignment are wall-calendar steps (``dateutil.relativedelta``) in the term's
zone: the anchor's zone, else the project ``TIME_ZONE``. A term over an instant
resolves to an instant and its local date; a term over a local day resolves to a
local day until a sub-day step or :meth:`Align.at_time` makes it an instant.

**Counters.** A counter term's amounts are ``Decimal`` values in the counter's
own unit; a rule never converts units (``angee.uom.Uom.convert`` does, at the
consumer that reads the counter). A :class:`Counter` with a rate projects the
instant its target is reached, which lets it compete with calendar terms.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Mapping
from datetime import UTC
from decimal import Decimal
from typing import Annotated, Any, Literal, Self

from dateutil.relativedelta import FR, MO, SA, SU, TH, TU, WE, relativedelta
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from angee.base.validators import validate_value
from angee.scheduling.recurrence import Recurrence
from angee.scheduling.zones import ZoneName, require_aware, zone_info

type CalendarUnit = Literal["minutes", "hours", "days", "weeks", "months", "years"]
type Period = Literal["day", "week", "month", "quarter", "year"]
type Weekday = Literal["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
type Combine = Literal["earliest", "latest"]
type Point = dt.date | dt.datetime
"""A calendar point: a local day, or an aware instant expressed in its term's zone."""

Instant = Annotated[AwareDatetime, AfterValidator(lambda moment: moment.astimezone(UTC))]
"""An aware moment, held and serialized in UTC so a basis spells one instant one way."""

type AnchorValue = Anchor | dt.datetime | dt.date | None
"""What a subject supplies per anchor name: an :class:`Anchor`, a bare instant or day, or ``None`` (not yet known)."""

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
        """The value as stable JSON, defaults left out: what a column stores."""

        return self.model_dump(mode="json", exclude_defaults=True)


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
    amounts count whole units. ``Offset(300)`` is 300 of the term's counter unit.
    """

    amount: Annotated[Decimal, Field(allow_inf_nan=False)]
    unit: CalendarUnit | None = None

    def __init__(self, amount: Decimal | int | str, unit: CalendarUnit | None = None, **data: Any) -> None:
        """Accept the amount and unit positionally, as a rule is read aloud."""

        super().__init__(**{"amount": amount, "unit": unit, **data})

    @model_validator(mode="after")
    def _counts_whole_calendar_units(self) -> Self:
        if self.unit is not None and self.amount != self.amount.to_integral_value():
            raise ValueError("A calendar offset counts whole units.")
        return self

    def apply(self, point: Point, zone: dt.tzinfo, *, sign: int = 1, edge: Literal["start", "end"] = "start") -> Point:
        """Step a calendar point by this offset times ``sign`` in ``zone``.

        Elapsed units promote a local day to its first instant, or its last for
        ``edge="end"`` (a grace window runs from the end of a due day).
        """

        if self.unit is None:
            raise ValueError("A counter amount does not step a calendar point.")
        amount = sign * int(self.amount)
        if self.unit in _ELAPSED_STEPS:
            moment = point if isinstance(point, dt.datetime) else _day_edge(point, zone, edge)
            return (moment.astimezone(UTC) + _ELAPSED_STEPS[self.unit](amount)).astimezone(zone)
        stepped = point + _CALENDAR_STEPS[self.unit](amount)
        return _in_zone(stepped, zone) if isinstance(stepped, dt.datetime) else stepped


class Align(_Value):
    """One alignment step, applied in order after a term's offset in the term's zone.

    - :meth:`start_of` / :meth:`end_of` a period (day, week, month, quarter, year);
      on an instant they keep the zone's first or last instant of that day.
    - :meth:`next_weekday` / :meth:`previous_weekday`: on or after (before) the
      point, or strictly after (before) it with ``strict=True``.
    - :meth:`at_time`: a local clock time on the point's local date, in ``zone``
      when named, otherwise the term's zone; the point becomes an instant.
    """

    kind: Literal["start_of", "end_of", "next_weekday", "previous_weekday", "at_time"]
    period: Period | None = None
    weekday: Weekday | None = None
    strict: bool = False
    time: dt.time | None = None
    zone: ZoneName = ""

    @model_validator(mode="after")
    def _fields_fit_the_kind(self) -> Self:
        needs = {"start_of": "period", "end_of": "period", "at_time": "time"}.get(self.kind, "weekday")
        given = {name for name in ("period", "weekday", "time") if getattr(self, name) is not None}
        if given != {needs}:
            raise ValueError(f"{self.kind} takes {needs} and no other target.")
        if self.strict and needs != "weekday":
            raise ValueError("strict applies to a weekday alignment.")
        if self.zone and needs != "time":
            raise ValueError("zone applies to at_time.")
        return self

    @classmethod
    def start_of(cls, period: Period) -> Align:
        """The first day (first instant) of the period containing the point."""

        return cls(kind="start_of", period=period)

    @classmethod
    def end_of(cls, period: Period) -> Align:
        """The last day (last instant) of the period containing the point."""

        return cls(kind="end_of", period=period)

    @classmethod
    def next_weekday(cls, weekday: Weekday, *, strict: bool = False) -> Align:
        """The weekday on or after the point, or strictly after it."""

        return cls(kind="next_weekday", weekday=weekday, strict=strict)

    @classmethod
    def previous_weekday(cls, weekday: Weekday, *, strict: bool = False) -> Align:
        """The weekday on or before the point, or strictly before it."""

        return cls(kind="previous_weekday", weekday=weekday, strict=strict)

    @classmethod
    def at_time(cls, time: dt.time | str, *, zone: str = "") -> Align:
        """A local clock time (``"14:00"``) on the point's local date; blank ``zone`` is the term's zone."""

        return cls.model_validate({"kind": "at_time", "time": time, "zone": zone})

    def apply(self, point: Point, zone: dt.tzinfo) -> Point:
        """Return ``point`` aligned in ``zone``; an instant stays an instant, a local day a day."""

        day = point.date() if isinstance(point, dt.datetime) else point
        if self.time is not None:
            return _in_zone(dt.datetime.combine(day, self.time, zone_info(self.zone) if self.zone else zone), zone)
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

    ``zone`` names the wall clock the anchor's terms step in (a venue's zone);
    blank is the project ``TIME_ZONE``. ``readings`` snapshot counters at the
    anchor (``Anchor.reading(on=…, readings={"usage_hours": Decimal("6357.7")})``)
    for counter terms. A bare aware ``datetime`` or a ``date`` reads as an anchor.
    """

    at: Instant | None = None
    on: dt.date | None = None
    zone: ZoneName = ""
    readings: dict[str, Decimal] = Field(default_factory=dict)

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

    @classmethod
    def reading(
        cls,
        *,
        readings: Mapping[str, Decimal],
        at: dt.datetime | None = None,
        on: dt.date | None = None,
        zone: str = "",
    ) -> Anchor:
        """An anchor that also snapshots counter readings, such as the last time work was done."""

        return cls(at=at, on=on, zone=zone, readings=dict(readings))

    def point(self, zone: dt.tzinfo) -> Point:
        """Return the anchor as a calendar point in ``zone``."""

        if self.at is not None:
            return self.at.astimezone(zone)
        assert self.on is not None  # _one_moment admits an instant or a day
        return self.on


class Counter(_Value):
    """A counter's current state, supplied by its consumer: a reading, when it was taken, and an optional rate.

    ``rate_per_day`` (in the counter's unit) lets a counter term project the instant
    its target is reached; the consumer owns how the rate is measured.
    """

    reading: Decimal
    at: Instant
    rate_per_day: Annotated[Decimal, Field(gt=0)] | None = None

    def reaches(self, target: Decimal) -> dt.datetime | None:
        """Project the instant the counter reaches ``target`` at its rate, or ``None`` without one."""

        if self.rate_per_day is None:
            return None
        days = (target - self.reading) / self.rate_per_day
        try:
            return self.at + dt.timedelta(microseconds=int((days * _MICROSECONDS_PER_DAY).to_integral_value()))
        except OverflowError:
            return None


class Repeat(_Value):
    """What follows once the work a due point set is done (:meth:`DueRule.next`).

    - ``Repeat()``, the default: nothing; the rule ends once its due point is met.
    - :meth:`from_completion`: the rule resolves again with the completion as its
      ``anchor`` (``last_done`` by default), so the next due counts from the work.
    - :meth:`calendar`: the next occurrence of an RRULE strictly after the previous
      due point, independent of when the work was done. The series restarts at each
      due point, so a rule ends with ``UNTIL``; ``COUNT`` is refused.
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
        """The next due counts from the completion, supplied as ``anchor``."""

        return cls(kind="from_completion", anchor=anchor)

    @classmethod
    def calendar(cls, rule: str) -> Repeat:
        """The next due is the RRULE's next occurrence after the previous due point."""

        return cls(kind="calendar", rule=rule)


class Term(_Value):
    """One limit measured from a due anchor: a calendar term, or a counter term when ``counter`` is named.

    A calendar term steps the anchor by ``offset`` (a calendar unit), then applies
    ``align`` in order. A counter term's target is the anchor's reading of
    ``counter`` plus ``offset`` (an amount in the counter's unit). ``warn`` opens
    the warning window before the due point and ``grace`` allows the time or
    amount after it, in the term's own measure. ``label`` names the term in a
    rule with several and in the resolution's ``winner``.
    """

    label: str = ""
    anchor: str = Field(min_length=1)
    counter: str = ""
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


class TermDue(_Value):
    """One term's resolution inside a :class:`Due`; with the term itself it explains the point.

    ``origin`` is the anchor value the term started from, ``None`` while that
    anchor is pending. A calendar term carries ``at`` (an instant, aware UTC)
    and/or ``local_date``; a counter term carries ``target`` and, when its counter
    has a rate, the projected ``at`` with the ``forecast`` it came from. ``zone`` is
    the zone its calendar steps ran in, so status is read in the same zone later.
    """

    term: Term
    zone: ZoneName = ""
    origin: Anchor | None = None
    at: Instant | None = None
    local_date: dt.date | None = None
    target: Decimal | None = None
    forecast: Counter | None = None

    @classmethod
    def of(cls, term: Term, origin: Anchor | None, counter: Counter | None) -> TermDue:
        """Resolve one term from its anchor value (and its counter's state, for a counter term)."""

        if origin is None:
            return cls(term=term)
        zone_name = origin.zone or timezone.get_default_timezone_name()
        zone = zone_info(zone_name)
        if term.counter:
            if term.counter not in origin.readings:
                raise ValidationError({f"anchors.{term.anchor}.readings": [f"No {term.counter!r} reading."]})
            target = origin.readings[term.counter] + (term.offset.amount if term.offset is not None else 0)
            at = counter.reaches(target) if counter is not None else None
            return cls(
                term=term,
                zone=zone_name,
                origin=origin,
                target=target,
                at=at,
                local_date=at.astimezone(zone).date() if at is not None else None,
                forecast=counter if at is not None else None,
            )
        point = origin.point(zone)
        if term.offset is not None:
            point = term.offset.apply(point, zone)
        for step in term.align:
            point = step.apply(point, zone)
        if isinstance(point, dt.datetime):
            return cls(term=term, zone=zone_name, origin=origin, at=point, local_date=point.date())
        return cls(term=term, zone=zone_name, origin=origin, local_date=point)

    def deadline(self) -> dt.datetime | None:
        """The instant that orders this term against others: ``at``, or the end of a due day.

        A counter term without a forecast has none: it is compared by status only.
        """

        if self.at is not None:
            return self.at
        if self.local_date is not None:
            return _day_edge(self.local_date + dt.timedelta(days=1), zone_info(self.zone), "start").astimezone(UTC)
        return None

    def status(self, *, now: dt.datetime, counters: Mapping[str, Counter]) -> DueStatus:
        """Classify ``now`` (or the counter's current reading) against this term's point and windows.

        The comparison runs at the point's grain: a local day is due all that day,
        an instant or a counter target exactly at it. ``warn`` reaches back from the
        point; ``grace`` runs on from it (from the end of a due day).
        """

        term = self.term
        if term.counter:
            counter = counters.get(term.counter)
            if counter is None:
                raise ValidationError({"counters": [f"Status needs the current {term.counter!r} reading."]})
            assert self.target is not None  # a resolved counter term carries its target
            reading = counter.reading
            return _classify(
                lambda bound: reading >= bound,
                lambda bound: reading > bound,
                self.target,
                self.target - term.warn.amount if term.warn is not None else None,
                self.target + term.grace.amount if term.grace is not None else self.target,
            )
        zone = zone_info(self.zone)
        today = now.astimezone(zone).date()
        assert self.local_date is not None  # a resolved calendar term carries its local date
        point: Point = self.at.astimezone(zone) if self.at is not None else self.local_date

        def position(bound: Point) -> Point:
            return now if isinstance(bound, dt.datetime) else today

        return _classify(
            lambda bound: position(bound) >= bound,
            lambda bound: position(bound) > bound,
            point,
            term.warn.apply(point, zone, sign=-1) if term.warn is not None else None,
            term.grace.apply(point, zone, edge="end") if term.grace is not None else point,
        )


def _classify[B](
    reached: Callable[[B], bool], beyond: Callable[[B], bool], point: B, warn_from: B | None, grace_until: B
) -> DueStatus:
    """The one status ladder every measure shares, from the most severe rung down."""

    if beyond(grace_until):
        return DueStatus.OVERDUE
    if beyond(point):
        return DueStatus.GRACE
    if reached(point):
        return DueStatus.DUE
    if warn_from is not None and reached(warn_from):
        return DueStatus.WARNING
    return DueStatus.OK


class Due(_Value):
    """A rule's resolution: the due point, the counter targets, the winning term and the basis.

    ``at`` (aware UTC) and/or ``local_date`` come from the winning term among the
    dated ones (calendar terms and counter terms with a forecast). A counter term
    without a forecast cannot be placed on the calendar: its target is reported in
    ``counter_targets`` and it counts in :meth:`status`. While any anchor is
    ``pending`` there is no due point. ``previous`` is the due point a calendar
    repeat followed. ``basis`` is the canonical JSON to store; ``Due.model_validate``
    reads it back.
    """

    terms: tuple[TermDue, ...] = Field(min_length=1)
    combine: Combine = "earliest"
    pending: tuple[str, ...] = ()
    winner: str | None = None
    at: Instant | None = None
    local_date: dt.date | None = None
    counter_targets: dict[str, Decimal] = Field(default_factory=dict)
    previous: Anchor | None = None

    @classmethod
    def combined(cls, terms: tuple[TermDue, ...], combine: Combine) -> Due:
        """Combine resolved terms: the earliest (whichever first) or latest (whichever last) dated term wins."""

        pending = tuple(sorted({resolved.term.anchor for resolved in terms if resolved.origin is None}))
        if pending:
            return cls(terms=terms, combine=combine, pending=pending)
        pick = min if combine == "earliest" else max
        targets: dict[str, Decimal] = {}
        for resolved in terms:
            if resolved.target is not None:
                name = resolved.term.counter
                targets[name] = pick(targets[name], resolved.target) if name in targets else resolved.target
        dated = [
            (deadline, index) for index, resolved in enumerate(terms) if (deadline := resolved.deadline()) is not None
        ]
        if not dated:
            return cls(terms=terms, combine=combine, counter_targets=targets)
        # Ties keep the first-declared term in both directions.
        _, index = min(dated) if combine == "earliest" else max(dated, key=lambda item: (item[0], -item[1]))
        winner = terms[index]
        return cls(
            terms=terms,
            combine=combine,
            winner=winner.term.label,
            at=winner.at,
            local_date=winner.local_date,
            counter_targets=targets,
        )

    @property
    def basis(self) -> dict[str, Any]:
        """The resolution as stable JSON for the consumer's basis column."""

        return self.as_json()

    def status(self, *, now: dt.datetime, counters: Mapping[str, Counter] | None = None) -> DueStatus | None:
        """Return the status at ``now``, or ``None`` while an anchor is pending; never stored.

        ``earliest`` reports the most severe term, ``latest`` the least severe.
        Counter terms read the current ``counters`` reading, never the forecast.
        """

        require_aware(now=now)
        if self.pending:
            return None
        readings = validate_value(dict[str, Counter], dict(counters or {}), field="counters")
        statuses = [resolved.status(now=now, counters=readings) for resolved in self.terms]
        pick = max if self.combine == "earliest" else min
        return pick(statuses, key=_SEVERITY.index)


class DueRule(_Value):
    """A declarative due rule: labelled terms over named due anchors, combined and repeated.

    ``DueRule.of(Term(anchor="first_slot", offset=Offset(-48, "hours")))`` is due
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

    @classmethod
    def of(cls, *terms: Term, combine: Combine = "earliest", repeat: Repeat | None = None) -> DueRule:
        """Build a rule from its terms, as template data reads."""

        return cls(terms=terms, combine=combine, repeat=repeat or Repeat())

    def resolve(self, *, anchors: Mapping[str, AnchorValue], counters: Mapping[str, Counter] | None = None) -> Due:
        """Resolve the rule against the subject's anchors and its counters' current state; pure.

        A missing or ``None`` anchor leaves its terms pending and names it in
        ``Due.pending``. Anchor and counter errors raise Django's
        :class:`~django.core.exceptions.ValidationError` with paths under
        ``anchors`` and ``counters``.
        """

        known = validate_value(dict[str, Anchor | None], dict(anchors), field="anchors")
        states = validate_value(dict[str, Counter], dict(counters or {}), field="counters")
        terms = tuple(
            TermDue.of(term, known.get(term.anchor), states.get(term.counter) if term.counter else None)
            for term in self.terms
        )
        return Due.combined(terms, self.combine)

    def next(
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
        steps ``due``'s point to the RRULE's next occurrence in the term's zone,
        whatever ``done`` says.
        """

        match self.repeat.kind:
            case "once":
                return None
            case "from_completion":
                return self.resolve(anchors={**(anchors or {}), self.repeat.anchor: done}, counters=counters)
        if due.local_date is None:
            raise ValidationError({"due": ["A calendar repeat follows a resolved due point."]})
        [resolved] = due.terms
        start = due.at or _day_edge(due.local_date, zone_info(resolved.zone), "start")
        following = Recurrence(self.repeat.rule).after(start, start, zone=resolved.zone)
        if following is None:
            return None
        local_date = following.astimezone(zone_info(resolved.zone)).date()
        at = following if due.at is not None else None
        previous = Anchor(at=due.at, zone=resolved.zone) if due.at else Anchor(on=due.local_date, zone=resolved.zone)
        return Due(
            terms=(resolved.model_copy(update={"at": at, "local_date": local_date}),),
            combine=due.combine,
            winner=due.winner,
            at=at,
            local_date=local_date,
            previous=previous,
        )
