---
"@angee/ui": patch
---

A client row-model list pages its records (flat) or its root groups (grouped, each open
group carrying its records inside its page), so the pager's total, page count and the rows
on the page agree. Before, pages counted group header rows plus whatever records were open,
so a grouped list such as platform models stopped after two pages, and a `page` URL param
was discarded while the read was in flight. A grouping client list's pager counts groups,
record navigation positions a record among the records behind the page's groups, and a
grouped board shows the page's groups. One owner (`useSettledPageClamp`) keeps every
surface's page inside the settled page count.
