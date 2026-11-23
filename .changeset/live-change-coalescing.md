---
"@angee/refine": minor
---

Live changes from other writers reach Refine's resource hooks (`useOne`,
`useList` under live mode) and authored reads through one coalescing policy: a
300 ms quiet window with a 2 s maximum wait, so a record written several times a
second refetches its readers once per burst, with one merged delivery carrying
every changed id. A read still fetching when a burst flushes is no longer
cancelled: it commits its answer and refetches as soon as it settles, so a
continuous stream of changes no longer leaves a slow read on its loading state. A
caller's own writes still cancel and refetch at once. `AngeeLiveResource` gains an
optional `identifier`, the registered Refine resource name, which `createApp`
fills in; a host that builds its own live provider sets it with
`refineResourceIdentifier(resource)` from `@angee/metadata`.
