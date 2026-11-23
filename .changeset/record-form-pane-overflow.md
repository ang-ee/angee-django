---
"@angee/ui": patch
---

A record form fits the space it is given. Its tabs that do not fit move into a
"More" menu, keeping the selected tab visible, and two-column sections follow
the width of the sheet or drawer rather than the viewport (`FormGrid`'s
`adaptiveTwo` columns, which also never exceed two). A field placed in a tab
explicitly stays in that tab instead of being pulled into the form's header. In
a create form, a read-only title that is still empty reads after the model
("New task"), a routed create form's breadcrumb reads "New", and a read-only
status that is still empty is not shown.
