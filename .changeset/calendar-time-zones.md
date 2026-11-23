---
"@angee/ui": minor
---

A picked date-time is stored as the instant the viewer meant:
`formatDateTimeStorage` and `DATETIME_STORAGE_FORMAT` (now
`yyyy-MM-dd'T'HH:mm:ssxxx`, was `yyyy-MM-dd'T'HH:mm`) write the local wall time
with seconds and its UTC offset, so every `datetime` widget (record forms,
filters, form specs, action dialogs) sends an instant instead of a naive time
the server reads in its own zone. `calendarDay(date, timeZone)`, the
`yyyy-MM-dd` day an instant falls on in a zone, and `isStorageDate` are new, and
`dateFromUnknown` is now exported from `@angee/ui` as well as
`@angee/ui/widgets/date-format`. A date formatter given a time zone the browser
does not know reads in the viewer's zone and warns once instead of throwing.
`AbsoluteTime` and list date cells read a bare `yyyy-MM-dd` value as that date
with no time or zone, a filter chip shows a date, date-time or time operand as a
human value, and `CalendarView` refuses a drop that would turn an all-day
occurrence timed or the reverse. Upgrade: code that parses a widget's date-time
with `DATETIME_STORAGE_FORMAT`, or compares it with a `yyyy-MM-ddTHH:mm` string,
accepts the seconds-and-offset form (`2026-11-20T10:00:00+09:00`).
