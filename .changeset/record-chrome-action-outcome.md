---
"@angee/ui": patch
---

Add `useActionOutcomeMutation` and `useRecordChromeActionOutcome`, the un-settled
counterparts of the ActionResult mutation hooks, so a contributed record verb can
collect typed arguments in `ActionFormDialog` and let the dialog bind the outcome.
A typed-args action `submit` may resolve `null`/`undefined`, which the dialog
treats as a form-level failure.
