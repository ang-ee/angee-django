---
"@angee/ui": minor
---

An `EditableLineField` (an entry of `EditableLines`' `fields`, or of a form's
`lineFields`) may declare `readOnlyWhen(row, parentRow)`: the field's cell is
read-only on each row it returns true for, judged from the row and its owning
document (`null` when there is none). The rest of the row stays editable and
keeps its row actions, so a document's section or note row can lock, say, its
product and price cells.
