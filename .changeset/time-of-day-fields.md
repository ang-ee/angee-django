---
"@angee/metadata": minor
"@angee/ui": minor
"@angee/app": patch
---

Time-of-day fields (the GraphQL `Time` scalar, `HH:MM[:SS]` with no zone) can
be shown and edited. `@angee/metadata` gives a `Time` field the new `time`
widget and filter field type (`ResourceFilterFieldType` gains `"time"`), and
its client-side filtering and sorting compare clocks as SQL does (`09:30`
equals `09:30:00`). `@angee/ui` registers the `time` widget, which edits
through the browser's time control and stores a cleared time as `null`;
`formatTime` and `formatTimeInput` read a `Time` value as written, with no
zone; the filter editor offers a `time` field type. The generated web types map
`Time` to `string`.
