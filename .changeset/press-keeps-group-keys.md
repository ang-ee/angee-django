---
"@angee/ui": patch
---

A press on a grouped list's column header never drops the keys a group shares that the
list's order holds. Before, on a list grouped by status and ordered by status, rank, then
title, a press on Title (a tie-breaker), on Rank (the shown key, when it is not the
order's last) or on a column the order does not hold set that column alone: the status
key went, so a list grouped in the browser reordered its groups by the pressed column's
value on each group. Now each of those presses keeps the status key, behind the pressed
column, `title:asc,status:asc` or `rank:desc,status:asc`, so the pressed column shows its
direction and the URL and a reload keep the order; the groups keep their order, in the
browser and on the server, since they follow their own axis whatever a press sets (the
note on groups following their axis). A list grouped in the browser by a field its order
does not lead with, such as a grouping chosen in the toolbar, keeps its groups too.
For the pressed column itself a press is the table's own single-sort toggle, as before, its
key placed ahead of the group's keys, and a tie-breaker still sorts in its first direction.
A press on a grouping column's own
header turns its groups, ascending and descending in turn, never removing its key, and
the keys the rows follow go; with several grouping levels the other levels' keys stay. A list that
groups nothing presses as before.
