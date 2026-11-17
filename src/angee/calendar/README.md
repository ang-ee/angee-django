# Calendar

Calendar owns calendars, their events and their attendees. Recurrence and time
zones stay with [`angee.scheduling`](../scheduling/README.md); people and addresses
stay with [`angee.parties`](../parties/README.md). This README is the inventory of
what the addon expresses; the [models](models.py), the [permissions](permissions.zed)
and the [schema](schema.py) own the exact contracts.

## Calendars

A `Calendar` belongs to one `parties.Party`: a person, an organization, or a
consumer's child of one. It carries a name, a description, a colour, a time zone
(blank is the project `TIME_ZONE`) and an overlap policy.

**No default calendars.** The addon creates no calendar on its own: not on a
person's first visit and not per organization. A calendar is created explicitly,
by whoever may edit its party, and the demo data seeds the ones the demo needs. A
consumer that wants a default calendar per record of its own creates it in its own
addon.

**A personal calendar on request.** `Calendar.objects.ensure_personal(user)` (the
`ensure_personal_calendar` verb) returns the user's own calendar, on their person
and owned by them, creating it the first time under their own create gate. It locks
the person first, so it is idempotent. The event form offers it to a user who has
no calendar to write to; nothing calls it for them.

## Events

A `CalendarEvent` belongs to one calendar. It stores one master row with an
optional RRULE. An exception to one instance of a recurring master is an override
row (the RFC 5545 `RECURRENCE-ID` shape): `series` names the master and
`original_start` the instance it replaces. A cancelled override drops its instance.
An event's calendar, series and instance are set once.

A series owns its overrides. When a master's start, all-day flag, rule or zone changes,
or the calendar zone it follows changes, each override's instance moves with the
master's wall clock. An override that no longer matches an instance becomes a
standalone event if it carries edits, and is deleted if it was a cancellation. One
a sync still links (a record link names it) is left for that sync to remove. A
cancelled master hides its moved instances too.

- **Status** is `confirmed`, `tentative` or `cancelled`; a cancelled event shows no
  occurrence.
- **Transparency** is `busy` or `free`; a free event never conflicts and never shows
  as busy time.
- **Visibility** is `default` (the calendar's readers read it), `public` (its
  free/busy viewers read it too) or `private` (only its writers read it), as
  Google Calendar's event `visibility` means them. An override inserted without
  a visibility or transparency takes its master's; a value the input or a sync
  supplies is kept.
- **Time zone:** an event may name its own zone (`timezone`), as RFC 5545's
  `DTSTART;TZID` and Google's `event.start.timeZone` do; blank means its
  calendar's. That zone is the wall clock its recurrence, its all-day dates and
  its overlap check expand on (`CalendarEvent.expansion_zone`), so a Berlin series
  on a New York calendar keeps its Berlin time. Changing a calendar's zone moves
  only the events without a zone of their own: an all-day event keeps its local
  dates, and every series derives its end again and rebases its overrides.
- **All-day** events start and end at midnight in their zone, a whole number of
  days apart; a form that sends another time is refused, and a drag lands on the
  nearest midnights.
- **Tags, chatter and history** come from `TaggedModel`, `ThreadedModelMixin` and
  `HistoryMixin`.
- **Attendees** are the `through` rows of `CalendarEvent.attendees` to
  `parties.Handle`: an invitation names an address, and the person is the handle's
  resolved party. The organizer is the attendee whose role is `organizer`, at most
  one per event.

## Occurrences

`event_occurrences(window_start, window_end)` returns what a calendar shows in a
half-open window of at most 400 days (`CalendarEvent.occurrence_window_limit`),
expanded server-side in each calendar's zone.

- **Ids:** every instance of a series has the id `<series_sqid>:<original start,
  UTC basic>`, overridden or not, and a plain event has its own `<event_sqid>`;
  `event_sqid` names the row to open.
- **Dragging:** an occurrence is `editable` when it is a single row the viewer may
  write. `reschedule_event` drags it.
- **Bounds:** each series stores where it ends (`series_end`, when every rule it
  holds carries `UNTIL` or `COUNT`), so reads skip series that ended before their
  window. An event's rule repeats at most hourly, and one expansion yields at most
  `MAX_OCCURRENCES` (10,000, scheduling's one cap) occurrences: past it the save or
  read is refused, never truncated.
- **Live updates:** `changes(CalendarEvent)` sends readers the change as usual, and
  sends free/busy viewers only that an event's busy time changed, so they refetch
  the redacted occurrences. A free event reaches them only through a partial save
  that names its transparency.

## Overlap

A calendar whose overlap policy is `refuse` rejects a busy event any of whose
instances meets another busy occurrence on it. A single event is checked over its
`[start, end)`. A series is checked instance by instance, up to its stored
`series_end` for a bounded rule, or two years from its start for an open-ended
one (`CalendarEvent.overlap_horizon`). Free and cancelled events never conflict,
and neither do a cancelled master's moved instances.

- **Where it runs:** `CalendarEvent.save`. A save that can change an event's time
  locks the calendar row (on any policy), then the event, then writes, rebases a
  master's overrides, and only then judges the stored result; a refusal rolls the
  whole save back and reaches a form, a drag or a sync as a field error. Every save
  path is covered, and two concurrent saves cannot take the same time. Queryset
  `update()` and `bulk_create` skip `save` and are not checked.
- **What it reveals:** the refusal names no other event. It runs only after the
  REBAC save gates admitted the write, so a save the actor may not make is refused
  for its permission before any other event is read.

## Access

A calendar is the access root of its events.

- The calendar's **owner** (its creator by default, transferable) reads, writes,
  shares and deletes it, and transfers it.
- The **holder** (the user of the person whose calendar it is) reads, writes,
  shares and deletes it, even when system work created it.
- **Sharing** grants `reader`, `editor` or `free_busy` through the generic
  record-access surface; the owner, the holder and editors share. A `free_busy` grantee reads
  no event: the occurrence read shows them busy time only, without title or
  details, and nothing for free events.
- An event's readers read its attendees' addresses.
- **Platform admins** reach every calendar.
- **Consumers** widen `read_granted`, `write`, `share`, `delete` and `transfer` from
  their own `permissions.extends.zed`, as a company scope does.
- **Writable calendars:** the `calendars` resource filters on `writable` (the
  viewer's `write`), which is what an event may be added to.

## Web

The fragment ships the Calendar app:

- the calendar page, with one toggle per listed calendar, named with its owning party;
- the event form, whose calendar picker (`angee.calendar.calendarPicker`) offers the
  calendars the viewer writes, defaults to the only one, leaves a choice of several
  to the viewer, and offers a viewer with none "Create my calendar";
- the calendar list and record pages, with the platform's share control.

Other addons overlay their own dated rows on the calendar page by contributing an
occurrence source (an `OccurrenceSourceContribution`) to the `calendar.events#occurrence-sources`
container; the source's toggle key is the contribution's child id. The
[`projects_calendar`](../projects_calendar/README.md) bridge contributes the
viewer's open tasks by due date.
