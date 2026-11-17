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

## Events

A `CalendarEvent` belongs to one calendar. It stores one master row with an
optional RRULE. An exception to one instance of a recurring master is an override
row (the RFC 5545 `RECURRENCE-ID` shape): `series` names the master and
`original_start` the instance it replaces. A cancelled override drops its instance.
An event's calendar, series and instance are set once.

A series owns its overrides. When a master's start, all-day flag or rule changes,
or its calendar's time zone changes, each override's instance moves with the
master's wall clock. An override that no longer matches an instance becomes a
standalone event if it carries edits, and is deleted if it was a cancellation. A
cancelled master hides its moved instances too.

- **Status** is `confirmed`, `tentative` or `cancelled`; a cancelled event shows no
  occurrence.
- **Transparency** is `busy` or `free`; a free event never conflicts and never shows
  as busy time.
- **Visibility** is `default` (the calendar's readers read it), `public` (its
  free/busy viewers read it too) or `private` (only its writers read it).
- **Tags, chatter and history** come from `TaggedModel`, `ThreadedModelMixin` and
  `HistoryMixin`.
- **Attendees** are the `through` rows of `CalendarEvent.attendees` to
  `parties.Handle`: an invitation names an address, and the person is the handle's
  resolved party. The organizer is the attendee whose role is `organizer`, at most
  one per event.

## Occurrences

`event_occurrences(window_start, window_end, calendars)` returns what a calendar
shows in a half-open window, expanded server-side in each calendar's zone. A
recurring master's instance has the id `<event_sqid>:<UTC basic start>` and cannot
be dragged; a single row (a plain event or an override) has the id `<event_sqid>`
and can. `reschedule_event` drags a single row.

## Overlap

A calendar whose overlap policy is `refuse` rejects a busy event whose
`[start, end)` meets another busy occurrence on it, expanded recurrences
included. Free and cancelled events never conflict. `CalendarEvent.clean` reports
it to a form; `CalendarEvent.save` checks it again under the calendar's row lock,
on every save path, so two concurrent saves cannot take the same time. The
refusal names no other event, and an actor whose save would be refused anyway is
judged only against the events it can read.

## Access

A calendar is the access root of its events.

- The calendar's **owner** (its creator by default, transferable) reads, writes,
  shares and deletes it, and transfers it.
- The **holder** (the user of the person whose calendar it is) reads, writes and
  deletes it.
- **Sharing** grants `reader`, `editor` or `free_busy` through the generic
  record-access surface; the owner and editors share. A `free_busy` grantee reads
  no event: the occurrence read shows them busy time only, without title or
  details, and nothing for free events.
- An event's readers read its attendees' addresses.
- **Platform admins** reach every calendar.
- **Consumers** widen `read_granted`, `write`, `share` and `delete` from their own
  `permissions.extends.zed`, as a company scope does.

## Web

The fragment ships the Calendar app:

- the calendar page, with one toggle per calendar the viewer reads;
- the event form;
- the calendar list and record pages, with the platform's share control.

Other addons overlay their own dated rows on the calendar page by contributing an
occurrence source to the `calendar.events#occurrence-sources` container. The
[`projects_calendar`](../projects_calendar/README.md) bridge contributes the
viewer's open tasks by due date.
