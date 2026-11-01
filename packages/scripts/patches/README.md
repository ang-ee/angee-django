# Refine query lifecycle repairs

`refinedev-core-5.0.12.patch` retains the six query-context source changes from
[Refine PR #7426](https://github.com/refinedev/refine/pull/7426), commit
`f8e4c53f7aa83eff6563d62a9f09fc71d513e0c3`. It also corrects native
`useInvalidate` to pass `invalidationOptions` as the second argument to
`QueryClient.invalidateQueries(filters, options)`, preserving the declared
`cancelRefetch: false` default, explicit cancellation and `throwOnError`.
The same patch preserves explicit delegated pessimistic updates:
`mutationMode: "pessimistic"` with `invalidates: []` creates no optimistic state,
so native `useUpdate.onMutate` neither cancels the entire resource nor snapshots
it for rollback. Its caller owns exact response-time acceptance/reconciliation.
Default, optimistic and undoable mutation policies keep their native behavior.
Remove each repair when a released core includes it; remove the patch and its
install-policy entry when all repairs are adopted.

Generate the patch in a disposable workspace with native `pnpm patch` and
`pnpm patch-commit`. Start from the published `@refinedev/core@5.0.12` tarball,
apply the query-context hunks, native invalidation argument correction and
narrow delegated pessimistic completion correction, then run upstream core's `npm run build`
(`tsup` followed by `packages/shared/generate-declarations.js`). Obtain its
`tsup.config.ts`, declaration/build tsconfigs and shared build helpers from the
published core tag below. The build uses tsup 6.7.0, TypeScript 5.8.3 and the native dependency APIs
from core's manifest, including TanStack Query 5.81.5. Include the regenerated
CommonJS, ESM, declaration files and source maps in the patch.

Before applying the source fix, the same build must reproduce every published
artifact byte for byte. Only artifacts changed by these native source repairs belong
in the patch. The provider's native lifecycle tests cover all five affected hooks
and both providers that consume cancellation and providers that leave signals unread.
The same real-provider suite seeds committed data and holds a native refetch to
verify false/default/true invalidation options, initial-fetch joining, native
selector/filter precedence and error propagation. This repair changes argument
forwarding, not live/mutation invalidation policy. The published core source
matches official tag `4d60acfd919461f319c71b52195aff0c23ce904f`.

## Hasura read cancellation

`refinedev-hasura-7.0.1.patch` forwards Refine's per-request lazy `meta.signal`
through graphql-request's native RequestOptions for getOne/getMany/getList and
custom authored/generated reads. Native writes keep their existing dispatch and
do not acquire Query cancellation. Remove this patch and its policy entry when
a released Hasura provider includes this behavior, after running the native
real-provider cancellation and non-consuming-provider lifecycle tests.

Provenance: published `@refinedev/hasura@7.0.1` tarball (npm SHA1
`fde97bda3fddf995bb0441f38c489703aafa646b`), official tag commit
`c09e5697ead10addfa4a81f11fad441350072e1a`. Build the published TypeScript
with that tag's `packages/hasura/tsup.config.ts`, shared esbuild plugins, root
`tsconfig.build.json`, declaration configs and shared declaration builder. Native
tsup 6.7.0 and TypeScript 5.8.3 first reproduced every published dist member byte
for byte. Apply the source-only dispatch change, run the same native `npm run
build`, then use `pnpm patch`/`pnpm patch-commit` to capture only changed source
and generated outputs. Never edit dist by hand.
