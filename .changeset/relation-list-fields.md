---
"@angee/metadata": minor
"@angee/ui": minor
---

A to-many relation field defaults to the `many2many` relation multi-select instead of
the free-text tag input. Record forms render it with chips, save it as a public id
list and treat a re-picked equal set as unchanged; read-only values are linked chips.
`RelationMultiFieldWidget` offers inline create from metadata like `RelationFieldWidget`
(`create={null}` declines it). Add `ChipList`, the one chip-list owner for value lists.
Remove the `defaultWidgetFor` alias; use `defaultWidgetForModelField` from `@angee/metadata`.
