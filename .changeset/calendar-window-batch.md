---
"@angee/ui": minor
"@angee/metadata": minor
---

`useCalendarWindow(sources, window, { hidden, enabled })` reads all of a page's
occurrence sources in one batch read, so a source set that grows or shrinks
never remounts the calendar grid, and sources sharing a document and variables
share one request; a source whose key is `hidden`, or that sets
`enabled: false`, leaves the batch. `@angee/metadata` exports
`canonicalResourceModelLabels(metadata, labels)`, the pure form of
`useCanonicalResourceModelLabels`. Upgrade: pass an array of sources where
`useCalendarWindow` took one (`[calendarWindowSource(source)]` for a typed
source), keep them on one data provider (mixed `dataProviderName`s now throw),
and drop the removed `CalendarWindowFetch` type; `CalendarWindowOptions` names
the new options.
