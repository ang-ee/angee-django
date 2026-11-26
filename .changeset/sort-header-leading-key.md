---
"@angee/ui": minor
"@angee/metadata": minor
---

A list header shows a sort only for the key its rows visibly follow: the first
key of the list's order that its groups do not share. A declared or default
order may hold several keys, the later ones only breaking ties, and every one of
them that has a column showed as sorted: a list grouped by kind and ordered by
kind, key and language showed "Language, ascending" over rows not in language
order, and the first press on that header sorted it descending. Now that first
key's header shows its direction and every other header shows "not sorted": a
later key's, and a key every record of a group shares. A group's records share
the value of a field the list groups by its whole value, a `Date` grouped by day
included, and the members of a relation it groups by (`owner.name` under
`owner`). A `DateTime` grouped by day, or a date grouped by month, still shows
the date's sort, since one group holds many of its values. The new
`GroupAxis.shares` in `@angee/metadata` answers which keys one group's records
share, and a header's `aria-sort` and its sort button read that one rule.

A press on a column that only breaks ties sorts it in its first direction
(ascending, for a list's columns) as a fresh single-key order, so every other
key goes, a grouping key's included; the table's toggle would have turned one
held ascending to descending. On a list grouped in the browser the groups then
lose the grouping key's order: TanStack orders them by the pressed column's
value on each group (a text column compares each group's first record, a number
column the group's sum) and leaves them in the rows' order for any other
column. A server-grouped list keeps its groups in place, since the groups read
keeps its own order. The press is decided from the table's state when it happens: while
a read is in flight the list shows the headers of its last settled read, and a
second press there is the toggle's next step, not the first press again. Every
other press is the table's own toggle, as before: on a header that shows a sort,
on a column the order does not hold (its first press sorts ascending), and on a
grouping column. On a list grouped in the browser, a grouping column's key
orders the groups, and its press turns them: `[status asc, rank asc]` grouped by
status becomes `[status desc]`, and the open group comes before the closed one.
On a server-grouped list the same press sets the same order, but the groups read
keeps asking for the groups in their own order, so the groups do not move; it
asks for each group's records in `status desc` alone, so they no longer follow
the rank tie-breaker, and the Rank header turns to "not sorted".

A press on the order's last key behind a grouping key flips that key in place,
and the URL and a saved view keep the whole order it leaves.

What does not change: the table's sorting holds the whole order, so every read
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
