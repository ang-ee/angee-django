---
"@angee/refine": minor
"@angee/ui": patch
---

`useAuthoredQueryBatch` keeps its Map and each entry's identity until a read its
callers use changes, so a grouped list no longer rebuilds on every render. Its
entries are now `QueryBatchEntry` (exported): a readonly `data`,
`dataUpdatedAt`, `error`, `isFetching` and `refetch`, no longer the whole
`UseQueryResult`. The new `useDerivedBatch(batch, derive, inputs)` maps a batch
and reuses each output while its entry is the same object and `inputs` are
unchanged. A group payload the decoder refuses is now that read's `error` in
`useAngeeGroupByBatch` and `useAngeeFacets`, as it already was in
`useAngeeGroupBy`, instead of throwing in render, and in all three it is
reported once per settle as a failed read is (refine's auth check and error
notification). Upgrade: read any other query property (`status`, `isStale`)
from your own `useQuery`.
