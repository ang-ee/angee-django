---
"@angee/ui": minor
---

Pending states draw skeletons where the renderer knows the geometry:
`LoadingPanel` in every shape, `RecordHeader`'s pending and unavailable titles,
a loading `Select`'s value, `Combobox.Status`, and pane lists through the now
public `PaneRowsSkeleton`. A failed read ends the pending state.
