---
"@angee/ui": patch
---

A row whose implementation key the registry no longer names reads that column as
null, and the console says so instead of showing an empty value: a list cell of
an implementation-key column (one the resource's `implFields` names) reads "Not
registered" (`implementation.notRegistered`), and the record form marks the
field "Not registered" (an attention mark beside it, from the stored record, so
a create form never shows it) while its control still offers every registered
implementation. A null in any other column renders as before.
