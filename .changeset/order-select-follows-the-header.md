---
"@angee/ui": minor
---

A pane list's Order select shows what its headers show and chooses as a header press
does. Before, it checked the first key of the list's state: on a list grouped by status
and ordered by status then rank it checked "Status, ascending" while the Rank header
showed the sort, on a list whose order was inherited it checked nothing, and a choice set
that column alone, so a list grouped in the browser lost its grouping key and reordered
its groups. Now it checks the first key the rows follow, the one `headerSort` shows on its
column, and a choice keeps the keys a group shares ahead of the chosen column, as a press
does (`status:asc,title:desc`). It offers only what it can show as chosen: a column its
header can sort, and not one whose key every group's records share (a grouping column's,
which no header shows as sorted; its own header still turns its groups). Choosing what it
already shows changes nothing. The list view's and the collection tree's panes both read
it from their table.

**Changed API.** `ResourceToolbarProps.sorting` and `ResourceToolbarProps.onSortingChange`
are replaced by `order`, a `ListOrderChoice` (`{ shown?: { field, desc }, offers(field),
choose(field, desc) }`). `listOrderChoice` and `ListOrderChoice` are exported from
`@angee/ui/views/resource-view-list-body`. **Upgrade:** a pane toolbar of your own over a
TanStack table passes `order={listOrderChoice(table)}` from that entry.
