---
"@angee/ui": minor
---

A row action declared with `defineRowAction` titles its outcome toast by outcome:
its `toast` (`RowActionToastCopy`) takes `successTitle` and `failureTitle` in
place of the single `title`, so a refused verb no longer toasts the wording of a
success. `toast.description` stays the failure reason when the server gives none.
Upgrade: replace each row action's `toast.title` with a `successTitle` and a
`failureTitle`.
