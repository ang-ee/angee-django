# Scheduling

Scheduling owns time as values: recurrence, due rules and named time zones. It has
no model, no public id and no permissions; consumers compose its fields and pure
value objects. [`SchedulingConfig`](apps.py) holds the addon contract, including
the working-time face that waits for its first consumer. This README is the one
inventory of what the addon expresses; the classes own the exact contracts.

## Named time zones

A zone is stored and exchanged as its IANA name, never as a `tzinfo`, so one value
crosses a column, a GraphQL `String`, a form and a Pydantic input. A model composes
[`TimeZoneField`](fields.py); a Pydantic input annotates `ZoneName`
([zones](zones.py)). Both refuse a name the installed zone database does not list.
A blank name names no zone: it takes its context's zone, and the project
`TIME_ZONE` when the context names none (a row's field has no context; a due-rule
term's blank zone is its anchor's). `get_zone_info(name)` returns the zone
computation uses. A naive datetime is refused everywhere (`require_aware`);
scheduling never guesses an offset.

## Recurrence

[`RecurrenceField`](fields.py) holds an RFC-5545 RRULE and
[`Recurrence`](recurrence.py) expands it: `occurrences(dtstart, start, end,
zone=…)` over a half-open window, `occurrences_after(dtstart, after, until, zone=…,
limit=…)` over `(after, until]`, and `get_occurrence_after(dtstart, moment,
zone=…)` for the next occurrence. Expansion runs on the wall clock of the named
zone (a venue's), or of the project zone when it is blank. An hourly or finer rule
follows the wall clock through a DST change: the missing hour in spring lands on the
next instant, which counts once, and the repeated hour in autumn runs once.

Expansion is bounded. A rule whose day filters can never match
(`BYMONTH=2;BYMONTHDAY=30`) is recognised in one 400-year calendar cycle and expands
to nothing, instead of letting dateutil scan to year 9999. `occurrences_after`
refuses a window holding more than `MAX_OCCURRENCES` unless the caller pages with
`limit` and resumes after the last occurrence it got.

## Due rules

A [`DueRule`](due.py) says when a record is due, measured from **due anchors**:
named points the subject learns over time. What a rule can express:

- **Terms.** A `Term` measures one anchor, in one zone (its own `zone`, else the
  anchor's, else the project zone). A calendar term steps the anchor by an
  `Offset`: minutes and hours are elapsed time; days, weeks, months and years are
  wall-calendar steps, and a month from 31 January is the last day of February.
  A counter term (`counter="usage_hours"`) targets the anchor's reading plus an
  amount in the counter's own unit. An offset reaches at most a thousand years, or
  10**12 counter units.
- **Alignment**, applied in order after the offset: `Align.to_start_of` and
  `to_end_of` a day, week (ISO, from Monday), month, quarter or year;
  `to_next_weekday` and `to_previous_weekday`, on or strictly after or before;
  `to_time("14:00")`, a local clock time that makes the point an instant; and
  `to_local_day()`, which drops an instant to its local date.
- **Windows.** `warn` opens a warning window before the due point and `grace`
  allows time or amount after it, in the term's own measure.
- **Combination.** Several labelled terms combine as `earliest` (whichever comes
  first) or `latest` (whichever comes last).
- **Forecast.** A `Counter(reading=…, at=…, rate_per_day=…)` with a positive rate
  projects when its target is reached, so the counter term can win against
  calendar terms; without a rate, or with a zero rate, its target is reported in
  `Due.counter_targets` and counts only in status. Unit conversion is
  `angee.uom.Uom.convert`, at the consumer that reads the counter.
- **Repeat.** `Repeat()` ends with the work; `Repeat.from_completion()` resolves
  again from the completion (`last_done` by default); `Repeat.from_rrule(rule)`
  steps to the RRULE's next occurrence after the previous point (after its whole
  day, for a local day). The RRULE ends with `UNTIL`; `COUNT` is refused.

A consumer row stores the rule beside its own date and a basis:

```python
from angee.scheduling.due import Anchor, Due, DueRule, Offset, Term
from angee.scheduling.fields import DueRuleField

class Item(...):
    due_date = models.DateField(null=True, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    due_rule = DueRuleField()                       # NULL: the date is set by hand
    due_basis = models.JSONField(default=dict, blank=True, editable=False)

item.due_rule = DueRule(terms=[Term(anchor="first_slot", offset=Offset(-48, "hours"))])
due = DueRule.from_json(item.due_rule).resolve(anchors={
    "created": item.created_at,                     # an aware instant or a date reads as an anchor
    "first_slot": Anchor(at=slot.starts_at, zone=venue.timezone) if slot else None,
})
item.due_at, item.due_date, item.due_basis = due.at, due.local_date, due.as_json()

basis = Due.from_json(item.due_basis)               # None for a blank basis
status = basis.get_status(now=timezone.now()) if basis else None
```

- **The subject owns the anchors.** Scheduling holds no anchors, counters or rates;
  the owner of the subject supplies them. An unknown anchor is listed in
  `Due.pending`. A single-term rule then has no point; under `earliest` the
  resolved terms still bound the point and the status (the pending anchor can only
  move it earlier); under `latest` there is no point and no status until every
  anchor is known.
- **`resolve` is pure.** It reads no database and no clock, so the same anchors give
  the same `Due` and the same basis. `Due.at` is the instant (UTC) when the point
  has a time of day; `Due.local_date` is the point's date in the winning term's
  zone. A point or window outside the calendar is a `ValidationError`.
- **The basis explains the point.** `Due.as_json()` names each term, its zone, the
  anchor value it stepped from (`origin`), its result, the winning term and any
  pending anchors; a repeated point names the RRULE that stepped it (`repeat`) and
  the previous point as its origin. It is canonical JSON: calendar amounts are
  integers, decimals are fixed-point without trailing zeros, instants are UTC, and
  defaults are left out, so a default is part of the stored contract.
- **Status is computed on read and never stored.** `Due.get_status(now=…,
  counters=…)` returns `ok`, `warning`, `due`, `grace` or `overdue`. A local day is
  due all that day; an instant is due only at that exact moment, so an instant term
  passes from `warning` to `grace` or `overdue` (align it `to_local_day()` to make it
  due for a day). A counter target is due exactly at the reading. `earliest`
  reports the most severe term, `latest` the least severe.
- **Reminders read the windows.** `Due.terms[i].warn_from` is where the warning
  opens (an instant for a calendar term, a reading for a counter term) and
  `grace_until` the last moment before overdue.
- **A hand-set date reads its status through the same rule.** Resolve
  `DueRule(terms=[Term(anchor="set", warn=…, grace=…)])` with
  `anchors={"set": the_date}` rather than writing another status ladder.
- **Repeat asks the rule.** `rule.resolve_next(due, done=…)` returns the next
  resolution, or `None` when the rule ends.
- **The field checks writes that save.** `full_clean` and an instance `save`
  validate and normalize the rule; `QuerySet.update()` and `bulk_update` skip that
  step ([`DueRuleField`](fields.py)).

Business-day offsets wait for the working-time face. Product vocabulary (anchor
names, kinds of limit, meters) stays in the consumer.
