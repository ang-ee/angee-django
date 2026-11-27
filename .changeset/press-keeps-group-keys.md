---
"@angee/ui": patch
---

A press on a grouped list's column header never drops the keys a group shares that the
list's order holds. Before, on a list grouped by status and ordered by status, rank, then
title, a press on Title (a tie-breaker), on Rank (the shown key, when it is not the
order's last) or on a column the order does not hold set that column alone: the status
key went, so a list grouped in the browser reordered its groups by the pressed column's
value on each group. Now each of those presses keeps the status key ahead of the pressed
column, `status:asc,title:asc` or `status:asc,rank:desc`, so the pressed column shows its
direction and the URL and a reload keep the order; the groups keep their order on the
server, and in the browser wherever the list's order leads with the keys its groups
share. A list grouped in the browser by a field its order does not lead with, such as a
grouping chosen in the toolbar, still reorders its groups on a press: by the pressed
column where its order does not hold the grouping key, and into the grouping key's own
order where the order holds it behind another key, since the press moves it ahead. Behind
the group's keys a press is the table's own single-sort toggle, as before, a tie-breaker
still sorting in its first direction. A press on a grouping column's own header turns
that key in place and the keys the rows follow go, as before; with several grouping
levels the other levels' keys now stay, each at its level, and a second press on a
descending grouping key turns it back to ascending instead of removing it. A list that
groups nothing presses as before.
