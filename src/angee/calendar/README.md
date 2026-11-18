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

A series owns its overrides. When a master's start, all-day flag, rule or zone changes,
or the calendar zone it follows changes, each override's instance moves with the
master's wall clock. An override that no longer matches an instance becomes a
standalone event if it carries edits, and is deleted if it was a cancellation. A
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
  window. An event's rule repeats at most daily and occurs within about 30 years
  of its start. A read costs its window, not the series' age: an open rule is read
  from the period before the window (scheduling's re-anchoring). At save, an open
  series is counted from its start to now plus the overlap horizon against
  `MAX_OCCURRENCES` (10,000, scheduling's one cap), and a `COUNT` series is read
  from its start; past the cap the save is refused, never truncated. An event's
  moments, and every occurrence's end, stay within the dates the calendar can
  reach; a row a read still cannot expand is skipped, never failing the read.
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

## Sources

A `CalendarSource` is a connected calendar account or a subscribed feed: an
`integrate.Integration` child, selected by `backend_class` from
`ANGEE_CALENDAR_SOURCE_BACKEND_CLASSES`. It syncs through integrate's pull
streams:

- **`calendars`:** one record per remote calendar. Its `RecordLink` names the
  mirrored calendar (on the owner's person, `source` naming the source), and the
  source states that calendar's name, colour and zone. A person's own rename
  outranks the source's by statement precedence, so it survives a remote rename.
- **`events`:** one stream per remote calendar, partitioned by its key. Remote
  event ids live only in its `RecordLink` rows: a master's key is its UID, an
  instance's its UID and `RECURRENCE-ID`. A removed master or single event is
  cancelled; a removed instance is deleted, which restores the master's occurrence.

Both streams use integrate's absence sweep, at most once per poll interval. A
record the source stops serving is unavailable after one sweep and tombstoned
after the stream's absence threshold (two sweeps); a tombstoned calendar
withdraws the identity its source stated. A source's event takes the event
owner's stored form: an untitled event is "(No title)", and at most 500
attendees are kept, the organizer first. An attendee lands on a handle by the
address the source gave, and a source never renames a handle that already exists.

A source's calendar is mirrored before its events land: a sync that finds it
missing retries on the next cycle.

- **Replicas.** A backend declares whether it takes writes. A local calendar
  always takes edits; a source's calendar only when its backend writes. On a
  read-only subscription the events and attendees are the source's replicas: a
  person creating, editing, rescheduling or deleting one is refused ("This
  calendar is a read-only subscription") on every save path, and its occurrences
  cannot be dragged. Only the source's own work writes them, and a handle merge
  still folds their attendees. The source owns its series: a moved series is never
  rebased locally, the source re-sends each instance under its own key, and a
  changed calendar zone re-reads the source's events instead of moving them.
- **Lifecycle.** The source's owner syncs it now and unsubscribes it. Unsubscribing
  disconnects the source at once; a job then removes its events, archives its
  calendar and retires its credential, and an hourly reconciler retries a purge
  that failed. A sync already running ends quietly at its next page instead of
  bringing events back. A source's calendar is never deleted on
  its own. How many sources one user keeps, and how
  fast they add them, are bounded (`ANGEE_CALENDAR_SOURCES_PER_OWNER`,
  `ANGEE_CALENDAR_SUBSCRIBE_RATE`).
- **ICS.** `calendar_integrate_ics` lets any signed-in user subscribe to a public
  feed (`webcal://` reads as `https://`); nothing is fetched until the first
  sync, which reports a feed it cannot read. The feed address is a secret, kept as
  the source's credential; the source shows only its host. The feed
  is the source's one remote calendar, polled hourly with a conditional GET; each
  event keeps its own `DTSTART` zone. Each fetched feed is a full snapshot; its
  module docstring lists the properties it maps.

The sources page lists them with their sync status. Source addons add their
subscribe or connect verb to its `calendar.sources#toolbar` container.

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
- **Consumers** widen `read_granted`, `write`, `share` and `delete` from their own
  `permissions.extends.zed`, as a company scope does.

## Web

The fragment ships the Calendar app:

- the calendar page, with one toggle per listed calendar, named with its owning party;
- the event form;
- the calendar list and record pages, with the platform's share control.

Other addons overlay their own dated rows on the calendar page by contributing an
occurrence source (an `OccurrenceSourceContribution`) to the `calendar.events#occurrence-sources`
container; the source's toggle key is the contribution's child id. The
[`projects_calendar`](../projects_calendar/README.md) bridge contributes the
viewer's open tasks by due date.
