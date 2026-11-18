# Due tasks on the calendar

The bridge overlays the viewer's open tasks on the calendar page, by due date. It
contributes one occurrence source to the calendar's
`calendar.events#occurrence-sources` container
([`calendar-source.tsx`](web/src/calendar-source.tsx)). Each task is an all-day,
read-only marker that opens the record the task is about, or the task itself.

Neither addon depends on the other: projects composes without a calendar, and a
calendar shows no tasks without this bridge. A host that wants both composes the
bridge.
