---
"@angee/app": minor
---

`defineAngeeWebViteConfig` writes a brotli and a gzip sidecar (`<file>.br` at
quality 11, `<file>.gz` at level 9) beside every `.js`, `.mjs`, `.css`,
`.html`, `.svg` and `.json` file a build emits that is 1 kB or larger, for an
ingress that serves a precompressed file. A sidecar that is not smaller than its
source is not written; fonts and images get none. A host needs no change.
