# Refine query and subscription fixes

`refinedev-core-5.0.12.patch` carries three fixes:

- exactly the six source changes from
  [Refine PR #7426](https://github.com/refinedev/refine/pull/7426), commit
  `f8e4c53f7aa83eff6563d62a9f09fc71d513e0c3`;
- `useInvalidate` passes its invalidation options (`cancelRefetch`) to TanStack
  Query's `invalidateQueries` as options instead of spreading them into the query
  filters, where they were ignored (`src/hooks/invalidate/index.tsx` and the built
  `dist/index.cjs`/`dist/index.mjs`). Reported upstream as
  [refinedev/refine#7656](https://github.com/refinedev/refine/issues/7656);
- `useSubscription` tracks the channel, provider, callback and serialized
  parameters/types/metadata, so navigation replaces a mounted subscription.
  Previously its effect tracked only `enabled`, retaining the old record and
  query key. The timeline's native hook test covers record and filter changes.

Remove each fix, and the patch with its install-policy entry once all are gone,
when the first Refine release containing it is adopted.

Generate the patch in a disposable workspace with native `pnpm patch` and
`pnpm patch-commit`. Start from the published `@refinedev/core@5.0.12` tarball,
apply those upstream source hunks, then run upstream core's `npm run build`
(`tsup` followed by `packages/shared/generate-declarations.js`). Obtain its
`tsup.config.ts`, declaration/build tsconfigs and shared build helpers from that
commit. The build uses tsup 6.7.0 (esbuild 0.17.19), TypeScript 5.8.3 and the native dependency APIs
from core's manifest, including TanStack Query 5.81.5. Include the regenerated
CommonJS, ESM, declaration files and source maps in the patch.

Before applying the source fix, the same build must reproduce every published
artifact byte for byte. Only artifacts changed by the upstream source fix belong
in the patch. The provider's native lifecycle tests cover all five affected hooks
and both providers that consume cancellation and Hasura, which does not.

# nuqs TanStack Router snapshot

`nuqs-2.10.1.patch` gives the TanStack Router adapter a `getSearchParamsSnapshot`
that reads the router's own location (`router.state.location.searchStr`). Without
it, nuqs' update queue starts each flush from `window.location.search`, which
drops other search params under memory or hash history. Reported upstream as
[47ng/nuqs#1636](https://github.com/47ng/nuqs/issues/1636); remove the patch and
its install-policy entry when the first nuqs release with the adapter fix is adopted.
