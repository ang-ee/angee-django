---
"@angee/ui": minor
---

`buildColumns` takes the list's resolved group axes instead of resolving them again:
`BuildColumnsOptions.groupStack` is replaced by `axes`, the `GroupAxis` list the table
state already resolves once for the table's grouping and its headers. Nothing a list
renders or reads changes. **Upgrade:** a caller of `buildColumns` from
`@angee/ui/views/resource-view-list-body` passes
`axes: tableGroupAxes(groupStack, metadata, columns, query)` from the same entry in place
of `groupStack`.
