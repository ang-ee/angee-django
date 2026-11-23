---
"@angee/refine": minor
"@angee/ui": minor
---

A payload that a collection source's `select` refuses no longer throws in
render: it becomes that read's error and is reported once per settle of the
read, as a failed read is. `@angee/refine` exports `useReportRefusedReads`, the
hook that does this reporting, beside `useDerivedBatch`; its facets and group
reads already use it.
