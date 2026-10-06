---
"@angee/ui": minor
---

A form's editable lines can be a declared section: `<Group label lines />` renders them
in its place in the record's sheet, titled by the group. Carried by a
`<model>#sections` child, it narrows like any section, so a product keeps only the
lines on a route with `only` under `when`; narrowed away, the lines are gone. A form
that declares no lines group shows them as its first pane beneath the sheet. `@angee/ui/runtime` adds
`composedContainerChildren`, a record container's declared children before narrowing.
