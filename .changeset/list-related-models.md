---
"@angee/ui": minor
"@angee/refine": minor
---

`List`, `ListView` and `ResourceList` take `relatedModels`, the model labels
whose changes refresh the list: a change on one of them whose related records
name any record of the list's model refetches it, including records outside the
current page that may enter its filter. Use it for columns and filters derived
from related rows; a list over an authored `source` declares its interests on its
own query instead. The dialect hooks behind them (`useAngeeAggregate`,
`useAngeeGroupBy`, `useAngeeGroupByBatch`, `useAngeeListBatch`) take the same
option, and a list's own and related changes share one coalesced refresh.
