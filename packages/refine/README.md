# @angee/refine

`@angee/refine` provides Angee's schema-independent Refine and Hasura transport, router, live-update, and typed-document glue; as a leaf package, it depends on no other Angee React package so the layers above it can reuse the transport contract without cycles.

Install: `pnpm add @angee/refine`

Resource lists pass their compiled query through `listQueryMeta`, which binds
the predicate and order to the stock Hasura provider's document override.
This package owns transport and native selections; resource capability and
query validation belong to `@angee/metadata`.

[React documentation](https://docs.angee.ai/react/) · [Package reference](https://docs.angee.ai/react/reference/refine/)
