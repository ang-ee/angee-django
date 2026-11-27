---
"@angee/ui": minor
"@angee/metadata": minor
---

A list header shows a sort only for the key its rows visibly follow: the first
key of the list's order that its groups do not share, and on a grouping column
the direction its groups go in. A declared or default
order may hold several keys, the later ones only breaking ties, and every one of
them that has a column showed as sorted: a list grouped by kind and ordered by
kind, key and language showed "Language, ascending" over rows not in language
order, and the first press on that header sorted it descending. Now that first
key's header shows its direction and a later key's shows "not sorted"; the header
of a key every record of a group shares shows the direction its groups go in (the
note on groups following their axis). A group's records share
the value of a field the list groups by its whole value, a `Date` grouped by day
included, and the members of a relation it groups by (`owner.name` under
`owner`). A `DateTime` grouped by day, or a date grouped by month, still shows
the date's sort, since one group holds many of its values. The new
`GroupAxis.shares` in `@angee/metadata` answers which keys one group's records
share, and a header's sort button and its `aria-sort` read that one rule: on a
grouping column the button and its arrow show the groups' direction and the
`aria-sort` says "none", since WAI-ARIA puts `aria-sort` on one header at a time,
the key the rows follow.

A press on a column that only breaks ties sorts it in its first direction
(ascending, for a list's columns) as the only key the rows follow, the keys a
group of the list shares, which no press drops, kept behind it, so every other key
goes; the table's toggle would have turned one held ascending to descending. The
press is decided from the list's live order when it happens: while
a read is in flight the list shows the headers of its last settled read, and a
second press there is the toggle's next step, not the first press again. Every
other press is the table's own toggle, those keys kept behind it: on a header that shows a sort
and on a column the order does not hold; a grouping column's press turns its groups between
ascending and descending. On a list grouped in the browser, a grouping column's key
orders the groups, and its press turns them: `[status asc, rank asc]` grouped by
status becomes `[status desc, rank asc]`, and the open group comes before the
closed one, its records still by rank.
On a server-grouped list the same press sets the same order, and the groups read
asks for the groups in that direction, so they turn too (the note on groups
following their axis); it
asks for each group's records in `status desc, rank asc`, so they keep following
rank, and the Rank header keeps its sort.

A press on the key the rows follow, when it is the order's last, turns it, the
grouping key kept behind it, and the URL and a saved view keep the whole order it leaves.

What does not change: the list's order holds every key, so every read
asks for each key and a list sorted in the browser keeps its tie-breakers; no
list reads or orders its rows differently until a person presses a header.

**Changed API.** `ariaSortForColumn` is removed from
`@angee/ui/views/resource-view-list-body`. **Upgrade:** a custom header renders
its cells with `ListHeaderCell` from the same entry, which sets `aria-sort` by
the rule above; no public function answers it alone. On a table of your own
`ListHeaderCell` leaves out the keys a group shares only when that table sets
`meta.groupAxes`; without it the header shows the table's first sort key.
`@angee/metadata` gains `GroupAxis.shares(field)`, and each list's TanStack
table carries the list's group axes as `meta.groupAxes`, which the headers read.

`@angee/ui` now augments TanStack's `TableMeta` with the optional `groupAxes`.
A program that imports `@angee/ui` and passes `meta` to a `useReactTable` of its
own declares its keys by augmenting `TableMeta` too (an undeclared key no longer
compiles), and must not name one of them `groupAxes`.
