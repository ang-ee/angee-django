# Refine query-context backport

`refinedev-core-5.0.12.patch` carries two fixes:

- exactly the six source changes from
  [Refine PR #7426](https://github.com/refinedev/refine/pull/7426), commit
  `f8e4c53f7aa83eff6563d62a9f09fc71d513e0c3`;
- `useInvalidate` passes its invalidation options (`cancelRefetch`) to TanStack
  Query's `invalidateQueries` as options instead of spreading them into the query
  filters, where they were ignored (`src/hooks/invalidate/index.tsx` and the built
  `dist/index.cjs`/`dist/index.mjs`). Not yet reported upstream.

Remove each fix, and the patch with its install-policy entry once both are gone,
when the first Refine release containing it is adopted.

Generate the patch in a disposable workspace with native `pnpm patch` and
`pnpm patch-commit`. Start from the published `@refinedev/core@5.0.12` tarball,
apply those upstream source hunks, then run upstream core's `npm run build`
(`tsup` followed by `packages/shared/generate-declarations.js`). Obtain its
`tsup.config.ts`, declaration/build tsconfigs and shared build helpers from that
commit. The build uses tsup 6.7.0, TypeScript 5.8.3 and the native dependency APIs
from core's manifest, including TanStack Query 5.81.5. Include the regenerated
CommonJS, ESM, declaration files and source maps in the patch.

Before applying the source fix, the same build must reproduce every published
artifact byte for byte. Only artifacts changed by the upstream source fix belong
in the patch. The provider's native lifecycle tests cover all five affected hooks
and both providers that consume cancellation and Hasura, which does not.
