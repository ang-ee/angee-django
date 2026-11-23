---
"@angee/ui": minor
---

A calendar range selection reports whether whole days were selected:
`CalendarView`'s `onSelectRange`, `CalendarViewSpec.onSelectRange` and
`ResourceListCalendarSpec.createDefaults` receive FullCalendar's all-day flag
as a third argument, `(start, end, allDay)`; when it is true, `end` is the
exclusive midnight after the last day. A quick-create can seed an all-day event
from a month-view day click instead of a midnight-to-midnight timed one.
