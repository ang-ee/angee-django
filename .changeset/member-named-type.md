---
"@angee/ui": patch
---

A member of an object field (a FormSpec `object` widget) named `type` shows its
message. Before, a value that broke such a member's rule showed nothing on
screen: a `MutationDialog` disabled Save, and a record form and an
`ActionFormDialog` built with `jsonSchemaActionArgs` refused the submit, with no
message and the member not marked invalid. Now its message shows under the
member as any member's does, stays through an edit that still fails, and goes
when the value is corrected. A member named `ref` still shows no message. When
the server refuses the object field and its member named `type` together, the
two errors meet at one key: if the member's refusal is applied first, its
message is lost and only the field's shows; if the field's is applied first,
both show, but `serverErrorsFromForm` returns only the member's. Which rules
fail and what a form saves or sends are unchanged.
