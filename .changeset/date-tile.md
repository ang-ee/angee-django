---
"@angee/ui": minor
---

`DateTile` (with `dateTileVariants`, in sizes `sm`, `md` and `lg`) shows a date as
its short month over a large day in the display face, in an optional `timeZone`.
A missing or unreadable date shows a pending state (the new `dateTile.pending`
message, or `pendingLabel`); otherwise the full date is the tooltip and what a
screen reader hears. `@angee/ui/widgets/date-format` adds `formatDateParts`, the
short month and the day of a date, each localized on its own.
