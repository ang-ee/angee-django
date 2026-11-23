---
"@angee/ui": minor
---

The delete preview's title, blocked count and selection count use plural forms,
so a one-record preview reads "Delete 1 record?". Their messages are now
`deletePreview.title_one` / `_other`, `deletePreview.blockedCount_one` / `_other`
and `deletePreview.recordCount_one` / `_other`. Upgrade: a host or addon that
overrides `deletePreview.title`, `deletePreview.blockedCount` or
`deletePreview.recordCount` renames its translation to the `_one` and `_other`
keys.
