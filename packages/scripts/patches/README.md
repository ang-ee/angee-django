# Refine query-context backport

`refinedev-core-5.0.12.patch` backports exactly the six source changes from
[Refine PR #7426](https://github.com/refinedev/refine/pull/7426), commit
`f8e4c53f7aa83eff6563d62a9f09fc71d513e0c3`. Remove this patch and its install-policy
entry when the first Refine release containing that fix is adopted.

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
