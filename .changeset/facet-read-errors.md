---
"@angee/refine": minor
---

`useAngeeFacets` returns each facet's own read's error as `errors`, by facet id,
beside the one `error` it already returned, so a facet whose read is refused
says why while the other facets keep their counts.
