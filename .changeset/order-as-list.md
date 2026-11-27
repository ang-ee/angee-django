---
"@angee/metadata": minor
"@angee/refine": minor
"@angee/ui": patch
---

A list read's order reaches the server as an ordered list, so a list ordered by two keys or
more reads in the priority it shows. A GraphQL server rebuilds an input object in its type's own
field order, so `order_by: { due: "desc", title: "asc" }` sorted by whichever of the two the
resource declares first; and the read's key, the list batch hash and a grouped list's scopes
compare an object with its keys sorted, so `due desc, title asc` and `title asc, due desc` were
one request, and a pure swap of priority (a saved view, a link, back and forward) read nothing
again.

`ResourceQuery.toOrderBy` returns one single-key object per key, in priority order
(`[{ due: "desc" }, { title: "asc" }]`), and an empty list for no order. `@angee/refine` names
that shape `ListOrderBy` and takes it where a list's order goes: `listQueryMeta`'s `orderBy`,
`GroupRowsRequest.orderBy` (`groupByVariables`' `rows_order_by`) and
`AngeeListBatchScope.orderBy`, which no longer accept an object. A list read's `sorters` go the
same way, one object per sorter in the order given, a dotted field nested, through
`@refinedev/hasura` 7.0.1 as Angee's pnpm install policy patches it: the stock `generateSorting`
merges them into one object. In `@angee/ui` the record pager's captured scope, the navigation
scope a list publishes and a gantt's bar order follow a pure priority swap.

**Upgrade:** pass `toOrderBy`'s result on as it is wherever a list's `order_by` goes. A caller
that wrapped it in a list (`[query.toOrderBy(order)]`) unwraps it, since the server refuses a
list inside a list; code that read its result as an object reads the list. A project that
installs through Angee's pnpm policy (`packages/scripts/pnpm-policy.cjs`) gets the
`@refinedev/hasura` patch; one that does not keeps the stock merge, so a read with several
`sorters` is ordered by the server's field order, not the sorters' priority.
