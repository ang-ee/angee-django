---
"@angee/app": minor
---

`defineAngeeWebViteConfig` writes a brotli and a gzip sidecar (`<file>.br` at
quality 11, `<file>.gz` at level 9) beside every `.js`, `.mjs`, `.css`,
`.html`, `.svg` and `.json` file a build emits that is 1 kB or larger, for an
ingress that serves a precompressed file. A sidecar that is not smaller than its
source is not written; fonts and images get none. The files go through a pool
sized from the machine's parallelism, at most 8 at once; `precompressConcurrency`
sets it, and the sidecars are the same bytes at any size. A host needs no change.
