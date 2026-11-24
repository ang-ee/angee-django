---
"@angee/ui": minor
---

Scrolling a list renders only the rows that enter its window: its other rows,
group headers and table header no longer re-render, and a resource view hands
its surfaces the same filter, grouping and sorting until they change.
`RecordRow` (from `@angee/ui/views/resource-view-list-body`) requires `cells`,
the row's visible cells, and no longer reads them itself, so it never draws
stale cells after a column is shown or hidden. `useVirtualWindow`'s item count
and size estimate are optional and default to the virtualizer's own;
`FlatListBody` now takes both from its `rowVirtualizer`. Upgrade: pass
`cells={row.getVisibleCells()}` to every `RecordRow` you render.
