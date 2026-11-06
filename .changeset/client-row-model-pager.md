---
"@angee/ui": patch
---

A client row-model list pages its records (flat) or its root groups (grouped, each group
open with its records inside its page), so the pager's total, page count and the rows on
the page agree; before, pages counted group header rows plus whatever records were open,
so a grouped list such as platform models stopped after two pages and a `page` URL param was
discarded while the read was in flight. Grouped client lists open their groups by default
like the server grouped list, and their toolbar pager counts groups. One owner
(`useSettledPageClamp`) keeps every surface's page inside the settled page count.
