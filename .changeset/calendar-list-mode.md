---
"@angee/ui": minor
---

`CalendarView` gains a `list` mode, a month's occurrences listed by day, beside
`month`, `week` and `day`; `CALENDAR_VIEW_MODES` and `CalendarViewMode` include
it, so the URL (`?mode=list`) and the toolbar's mode switch offer it. It steps
and is titled by month. `@angee/ui` now depends on `@fullcalendar/list`.
Upgrade: code that maps every `CalendarViewMode` exhaustively handles `list`.
