---
"@angee/ui": minor
"@angee/metadata": minor
---

Record forms and resource lists supply Archive / Unarchive, Move to trash /
Restore and Delete themselves, from the resource's metadata, so a page no longer
wires them. A page's `bulkActions` are added to these instead of replacing
Delete, a bulk action runs on at most 25 rows a click (the rest stay selected),
and Delete on a trashable resource is offered only for a row already in the
trash. Archived rows leave default lists, dashboards and relation pickers unless
a filter names the archive flag; every archivable list offers an "Archived"
filter, and `useRelationPickerSearch` (now exported) and
`useRelationPickerOptions` take `includeArchived` to list them. A list's row
selection defaults on only when the list has lifecycle actions, `bulkActions`
or a model that can be shared. A record verb marked `primary` now sits on the
toolbar without a `placement`, on a saved record the form's band fills up to two
buttons with its own unplaced, non-danger verbs, and an overflow (`⋮`) menu is
always the last control of its group; `RecordActionBar` takes `fillBand`, `genericActions` and
`contributedVerbs`, and `useTrashPrompt` an optional count. `@angee/metadata`
exports `archiveFlagField`, `unarchivedFilter`, `resourceLifecycle` (with
`ResourceLifecycle`), `canDeleteRecord` and `allowsRecordPermission`. Upgrade:
replace `RecordActionBar`'s `menuPlacement="leading"` with `menuTrigger="icon"`
(the trigger now follows the buttons) and drop `menuPlacement="trailing"`; stop
importing `useTrashActions` and `useTrashRowActions`; drop a page's own Archive,
Trash or Delete wiring and its `is_archived` filters; pass `selectable` to keep
selection on a list that has none of those actions; pass `resource` (or
`canRestore`) to `RemovedList` to keep its default Restore. `useBulkDelete` now
takes the list's internal bulk runner in place of the clear-selection callback,
so a page stops calling it and lets the list supply Delete.
