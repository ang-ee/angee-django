---
"@angee/ui": minor
---

A grouped list orders its groups by their own axis. Before, a list grouped in the browser handed
the table its whole order, so the table sorted the groups by every key of it: by the sum of a
number column, by a group's first record's text, never by a date's month or day, and by the axis
only when the order led with the axis's key. A list grouped on the server always asked for its
groups ascending, so a press on its grouping column changed nothing a person saw. Now each level's
groups go by label, then identity, in the direction the list's order holds for a key the axis
shares or for the field it buckets, else ascending (`GroupAxis.direction`), the group with no
value last in both directions, and on two levels the inner groups under an outer group with no
value ordered as under any other: the table state hands the table each axis's key, ascending,
ahead of the list's other keys, the axis's column comparing its groups in that direction, and the
server's groups read asks for the same direction. The list stores, links and saves its order as
before, with no key a person never chose, and URLs and saved views written before read as they
did.

What a person sees change: a browser list whose order does not lead with its axis now lists its
groups by the axis (an order holding the key behind another, an order without it, a grouping
chosen with "Group by", a date by month or by day, two levels whose order holds only the inner
key); a server list whose order holds its group key descending lists its groups descending; a
related record with no name comes after every name in both directions, in the browser as from the
server. A column whose key a group axis shares, the grouping column, shows its groups' direction
on its sort button and its arrow, never "not sorted", and its press turns them: ascending and
descending in turn, never removed, the first press writing the key descending where the order did
not hold it. Its header's `aria-sort` says "none", as an unsorted header's does: WAI-ARIA puts
`aria-sort` on one header at a time, and on a grouped list that is the header of the key the
records follow, which keeps it as before. A press on any other column, and a choice in the pane's
Order select, orders the records inside the groups and keeps the axis keys the order holds, now
behind the pressed key (`title:asc,status:asc`, where it was `status:asc,title:asc`). It does not
keep the date a level of months, quarters or weeks follows, which is a key the records order by and
no key the level shares: on a list grouped by month it turns the months back to ascending, as it
did before; keeping that date is a later change. A press on a
grouping column leaves the records' order alone: its key leads and every other key the order
holds stays behind it, in their order, the records' and another level's, so the records inside
the groups keep their order and the header of the key they follow keeps its `aria-sort`
(`status:desc,rank:asc` from `status:asc,rank:asc` or from `rank:asc,status:asc`). It drops only
the other keys its axis shares (a grouped relation's identity beside its name), and keeps its sort
button, and the focus on it. A date's months, quarters and weeks follow the date they bucket:
ordered by the date
descending, the newest bucket comes first, its records newest first, and with the date's key
cleared the buckets go ascending; the date's own header keeps its sort and its three-state press,
since a bucket's records do not share their date. A list grouped by a field no read can sort by
still lists its groups by their axis, and that field's header offers no press, and so shows no
direction.

**Changed API.** `@angee/ui` augments TanStack's `TableMeta` with `sorting` beside `groupAxes`:
the list's order, which `ListHeaderCell`, a header's sort button, `listOrderChoice` and a press
read, since the table's own `sorting` now leads with the axis keys, each ascending.
`buildColumns` takes each axis's direction (`BuildColumnsOptions.directions`, by position beside
`axes`) and gives the axis's column a comparator in that direction over each row's group key
read once per sort (`GroupAxis.compareGroups`, `GroupAxis.groupKey`, the identity the table's own
cached grouping value), `sortUndefined: false`, since the
library's `"first"` and `"last"` never tie two missing values and its default decides one from
the column's own value, and `enableSorting: true`, so the table always sorts its groups; it keeps
the press the column's header had as `meta.pressable`, which its sort button reads. A list
column's header is a stable component, so the columns built again when a group turns keep each
header mounted. `ListHeaderCell`'s `aria-sort` says "none" on a column whose key a group axis
shares, where its sort button shows the groups' direction. **Upgrade:** a table of your own that
sets `meta.groupAxes` and hands the table a sorting other than the list's order sets
`meta.sorting` to that order; one that hands it the list's order needs nothing. A table of your
own that calls `buildColumns` with axes and sorts its groups descending passes their
`directions` and hands each axis key ascending. A program that augments `TableMeta` must not name
a key `sorting`. Code that reads a grouped list's URL or saved order after a press finds the
pressed key first. Code that asks a `buildColumns` axis column's `getCanSort()` whether its header
can be pressed reads `meta.pressable`. Code that reads a grouping column header's `aria-sort` as
its groups' direction reads its sort button's name.

Not in this change: an absent `groupStack` or `pageSize` in a saved view; the nexus inbox's own
Order select; a client list in an inherited scope ignoring its `order`; the empty group's two
labels ("No owner" from the server, "No value" in the browser).
