---
"@angee/ui": patch
---

A record form's `object`, `list` or `rows` field no longer shows its loading
placeholder for good in a create form, in an edit form once the widget's code was
loaded, or for a record that lacks the field: the form gives an `object` field an
empty object and a `list` or `rows` field an empty list, as it gives a `json`
field an empty object. An untouched `object`, `list` or `rows` field is therefore
sent as an empty container (`{}` or `[]`) when a record is created, as untouched
`json`, `tagInput` and `many2many` fields already are; before, it was left out,
so the server's default or NULL applied.
