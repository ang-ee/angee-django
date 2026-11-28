---
"@angee/ui": minor
"@angee/refine": minor
---

A list can let a person select everything its read matches: `ListView` and
`List` take `selectAllMatching`, which offers "Select all N" where the pager
counts what a verb acts on, and a page's `bulkActions` then receives
`collection.allMatching` (`AllMatching`, `AllMatchingRecords` with the list's
`where`, or `AllMatchingGroups` with the groups read, its order included).
Opting in asserts that every page verb honours that mode or refuses it; the
built-in Archive, Trash, Restore, Delete and Share refuse while it is on.
`Checkbox` takes a `disabledReason`; from
`@angee/ui/views/resource-view-list-body`, `SelectionToggle` takes a
`disabledReason` and `ListSelectionBar` takes `matching`
(`ListSelectionMatching`) and `deleteDisabledReason`. `AngeeListBatchEntry`
from `@angee/refine` gains a required `updatedAt`, when the page last settled.
Upgrade: code that builds an `AngeeListBatchEntry` by hand (a test double, say)
sets `updatedAt`, `null` until the page has settled.
