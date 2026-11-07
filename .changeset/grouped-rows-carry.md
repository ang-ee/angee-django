---
"@angee/refine": minor
"@angee/ui": major
---

A grouped page is one request: the leaf groups read carries each group's first
page. `AggregateBucket.rows` holds a carried page, `GroupByRequestOptions.rows`
asks for it (`with_rows`, `rows_limit`, `rows_order_by`) and `groupRowsDocument`
adds `rows(limit, order_by)` to a generated groups document. The grouped surface
reads page 1 at the declared `leafPageSize` from the groups read and requests only
later or resized group pages; an authored source's `groups` document receives
`leafPageSize` and maps each group's first page to `bucket.rows`. A server-grouped
first page the groups read did not carry renders as an error.
