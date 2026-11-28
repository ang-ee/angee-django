---
"@angee/ui": minor
---

Edits made in a record form's lines are undo steps of the form's history: adding,
removing, duplicating or reordering a line is one step each, and typing in a cell
is another. `EditableLines` takes an optional `history` with the form's
`perform`, `start` and `commit`; a record form passes its own, so its
`onFieldInteractionStart` and `onFieldInteractionCommit` callbacks now also
receive a line cell's path (`lines.2.label`). `useFormHistory`'s `perform` reads
the form's values after its action, so a change that sends no values
notification (a field-array append, insert, remove or move) is recorded too.
`emptyLineRow` and `duplicateLineRow` now return a row that carries a client key
of its own. Upgrade: code that compares a row built by `emptyLineRow` or
`duplicateLineRow` with a literal allows for that key, and code that adds a line
to a form with undo builds it with one of the two and then performs it.
