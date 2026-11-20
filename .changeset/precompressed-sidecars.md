---
"@angee/app": minor
---

`defineAngeeWebViteConfig` writes a brotli and a gzip sidecar (`<file>.br` at
quality 11, `<file>.gz` at level 9) beside every `.js`, `.mjs`, `.css`,
`.html`, `.svg` and `.json` file a build emits that is 1 kB or larger, for an
ingress that serves a precompressed file. A sidecar that is not smaller than its
source is not written; fonts and images get none; the output is the same bytes
for the same input. The files go through a pool sized from the machine's
parallelism, at most four at once (zlib's work runs on libuv's four threads),
so a build with hundreds of assets holds no more in memory at a time than a
small one. The new option `precompressConcurrency` sets the pool's size; the
sidecars are the same bytes at any size. A host needs no change.
