# Scheduling

Scheduling owns time as values: recurrence, due rules and named time zones. It has
no model, no public id and no permissions; consumers compose its fields and pure
value objects. [`SchedulingConfig`](apps.py) holds the addon contract, including
the working-time face that waits for its first consumer.

## Named time zones

A zone is stored and exchanged as its IANA name, never as a `tzinfo`, so one value
crosses a column, a GraphQL `String`, a form and a Pydantic input. A model composes
[`TimeZoneField`](fields.py); a Pydantic input annotates `ZoneName`
([zones](zones.py)). Both admit a blank name, which means the project `TIME_ZONE`,
and refuse a name the installed zone database does not list. `zone_info(name)`
returns the zone computation uses. A naive datetime is refused everywhere
(`require_aware`); scheduling never guesses an offset.

## Recurrence

[`RecurrenceField`](fields.py) holds an RFC-5545 RRULE and
[`Recurrence`](recurrence.py) expands it: `occurrences(dtstart, start, end,
zone=…)` over a half-open window, and `after(dtstart, moment, zone=…)` for the next
occurrence. Expansion runs on the wall clock of the named zone (a venue's), or of
the project zone when it is blank.

## Due rules

A [`DueRule`](due.py) says when a record is due, measured from **due anchors**:
named points the subject learns over time. Each `Term` steps one anchor by a
calendar `Offset` (minutes and hours are elapsed time; days, weeks, months and
years are wall-calendar steps in the anchor's zone), applies `Align` steps in
order (start or end of a period, a weekday on, after or strictly after, a local
clock time), and may carry `warn` and `grace` windows. A counter term measures a
counter instead: its target is the anchor's reading plus an amount in the counter's
own unit. Several labelled terms combine as `earliest` or `latest`. `Repeat` says
what follows the work: nothing, a new resolution from the completion, or the next
occurrence of an RRULE.

A consumer row stores the rule beside its own date and a basis:

```python
from angee.scheduling.due import Anchor, Due, DueRule, Offset, Term
from angee.scheduling.fields import DueRuleField

class Item(...):
    due_date = models.DateField(null=True, blank=True)
    due_rule = DueRuleField()                       # NULL: the date is set by hand
    due_basis = models.JSONField(default=dict, blank=True, editable=False)

item.due_rule = DueRule.of(Term(anchor="first_slot", offset=Offset(-48, "hours")))
due = DueRule.model_validate(item.due_rule).resolve(anchors={
    "created": item.created_at,                     # an aware instant or a date reads as an anchor
    "first_slot": Anchor(at=slot.starts_at, zone=venue.timezone) if slot else None,
})
item.due_date, item.due_basis = due.local_date, due.basis   # None while due.pending
status = Due.model_validate(item.due_basis).status(now=timezone.now())
```

- **The subject owns the anchors.** Scheduling holds no anchors, counters or rates;
  the owner of the subject supplies them, and an unknown anchor leaves the rule
  `pending` with no point.
- **`resolve` is pure.** It reads no database and no clock, so the same anchors give
  the same `Due` and the same basis. `Due.at` is an aware instant when time of day
  matters; `Due.local_date` is the date in the term's zone.
- **The basis explains the point.** `Due.basis` is stable JSON naming each term,
  its anchor value, its zone and result, the winning term and any pending
  anchors; `Due.model_validate(basis)` reads it back.
- **Status is computed on read.** `Due.status(now=…, counters=…)` returns `ok`,
  `warning`, `due`, `grace` or `overdue` (`None` while pending) and is never
  stored. A local day is due all that day; an instant or a counter target exactly
  at it. `earliest` reports the most severe term, `latest` the least severe.
- **Counters stay in their own unit.** A `Counter(reading=…, at=…,
  rate_per_day=…)` with a rate projects when its target is reached, so it can
  win against calendar terms; without a rate its target is reported in
  `Due.counter_targets` and counts only in status. Unit conversion is
  `angee.uom.Uom.convert`, at the consumer that reads the counter.
- **Repeat asks the rule.** `rule.next(due, done=…)` returns the next resolution:
  from the completion (`Repeat.from_completion()`), or the RRULE's next occurrence
  after the previous point (`Repeat.calendar("FREQ=WEEKLY")`, which ends with
  `UNTIL`), or `None`.

Business-day offsets wait for the working-time face. Product vocabulary (anchor
names, kinds of limit, meters) stays in the consumer.
