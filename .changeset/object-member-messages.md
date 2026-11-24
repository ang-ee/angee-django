---
"@angee/ui": minor
---

A member of an object field (a FormSpec `object` widget) keeps its message. In a
`MutationDialog`, which checks every change, a value that broke a member's rule
disabled Save with no message on screen; in an `ActionFormDialog` and a record
form, which check from the first submit on, the member's message showed after a
refused submit and went at the next edit, though the value still failed. Now, in
a record form, a `MutationDialog` and an `ActionFormDialog` (unless its
definition brings a resolver other than the one `jsonSchemaActionArgs` returns),
a member's message behaves as a top-level field's: a `MutationDialog` shows it
as soon as the value fails, and these forms keep it through an edit that still
fails and remove it when the value is corrected; correcting one of two failing
members removes only that member's message. A form whose resolver is another one
an action form definition brings, or `createJsonSchemaResolver` used directly,
still loses a member's message at the next edit, and a member named `ref` shows
no message in any form. Which rules fail and what a form saves or
sends are unchanged. The error these forms' resolvers return for an object field
(a field with an `objectTemplate`) whose members failed now carries a `type` at
the field's own node, `MEMBER_ERRORS` (`"memberErrors"`), and no message of its
own; `@angee/ui/views/validation-errors` exports it with `markMemberErrors`,
which sets it from the fields a form shows. Code that compares such an error
tree deeply expects that `type`.
