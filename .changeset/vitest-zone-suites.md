---
"@angee/app": minor
---

The shared Vitest configs (`defineAngeePackageVitestConfig` and
`defineAngeeWebVitestConfig` from `@angee/app/vitest`) run zone suites under
pinned process time zones. A package with a `*.zones.test.ts` or
`*.zones.test.tsx` file runs as three projects: `default` (every suite, in the
runner's zone) and `TZ=Asia/Tokyo` and `TZ=America/Los_Angeles` (the zone suites
again, in forked processes). A package with no zone suite keeps its single
project.
