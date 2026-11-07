---
"@angee/ui": minor
"@angee/metadata": minor
---

Shared Trash for trashable resources. Resource metadata types the trashable
facet and `trashFlagField()` names its flag. `useTrashPrompt` is the one trash
confirmation (optional reason), `useTrashRecord` runs the shared
`trash_record` and `restore_record` verbs, `useTrashActions` and
`useTrashRowActions` offer Trash and Restore by the row's delete permission,
and `RemovedRecords` (with `RemovedDisclosure` and `RemovedItem`) lists a
resource's removed records.
