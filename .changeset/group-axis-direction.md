---
"@angee/metadata": minor
---

`GroupAxis` owns the order of an axis's groups. `GroupAxis.direction(order)` gives the
direction an axis's groups go in under a list's order: the direction of the first key of the
order the axis shares (`GroupAxis.shares`) or that is the field it buckets, so a date's months,
quarters and weeks follow the date, else ascending. `GroupAxis.groupKey(row, identity?)` reads a
record's group as groups compare, its label and then its identity (a caller that has read the
identity already passes it; the label is read only where the axis has its own, else it is the
identity), and `GroupAxis.compareGroups(left,
right, direction?)` compares two such keys in a direction, ascending unless asked: by label, then
identity, each in that direction, a missing one after every other in both directions and two
missing ones tied, so a comparison goes on to its next key. That is the same order of keys a
server's groups read asks for, not the same order of every text: text compares in the list's
natural order (`ResourceQuery.comparator`) where the server uses the database's collation, so
"item 9" and "item 10" can come in either order there. `GroupAxis.groupBy(direction?)`
orders its keys in that direction, ascending unless a caller asks, as facets and dashboards
leave it, and its missing value stays last either way. `ResourceQuery.toGroupRead(group,
filter?, order?)` takes a list's order and asks for the level's groups in the direction it gives
the axis; without an order it reads the resource's default order, as `toOrderBy` does.

`GroupProjection.orderBy`'s `direction`, and so `GroupRead.orderBy`'s, widens from `"ASC"` to
`"ASC" | "DESC"`. **Upgrade:** code that reads either and relied on an ascending order handles
`"DESC"`; a caller of `toGroupRead` that passes no order gets its groups descending only where
the resource's default order holds the axis's key descending.
