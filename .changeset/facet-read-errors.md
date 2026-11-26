---
"@angee/refine": minor
"@angee/ui": patch
---

`useAngeeFacets` also returns `errors`: each active facet's own read or decode
error, or `null`, by facet id; `error` stays the first of them. A hand-built or
mocked result must include `errors`. A facet's read opens no notice; the search
panel and the facet shortcut show a refused facet's words above the options its
last read still holds, and the shortcut's list is described by them.
