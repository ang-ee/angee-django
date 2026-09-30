# @angee/refine

`@angee/refine` provides Angee's schema-independent Refine and Hasura transport, router, live-update, and typed-document glue; as a leaf package, it depends on no other Angee React package so the layers above it can reuse the transport contract without cycles.

Install: `pnpm add @angee/refine`

Resource lists pass their compiled query through `listQueryMeta`, which binds
the predicate and order to the stock Hasura provider's document override.
This package owns transport and native selections; resource capability and
query validation belong to `@angee/metadata`.

Group summaries, facets, and grouped list scopes share a TanStack query by data
provider, printed GraphQL document, and variables. Scope labels address results
within a caller only. A second consumer of the same request reads the native
cache, including while the first request is in flight.
Failed group and facet reads use the authored error policy: they notify and run
Refine's `checkError`, so an authentication failure can end the session.
After a failed `useAngeeGroupBy` refetch, its last buckets remain visible with
the new error until a successful read replaces them.

[React documentation](https://docs.angee.ai/react/) · [Package reference](https://docs.angee.ai/react/reference/refine/)
