---
"@angee/app": minor
---

The shared Vitest defaults (`@angee/app/vitest`) unmount every DOM test after
it runs, as Testing Library does under Vitest globals, and run test files in
worker threads instead of forked processes. A file's own `afterEach` hooks run
before the unmount. Upgrade: a suite's own `afterEach(cleanup)` is now
redundant and can go; a hook that must tear down after the unmount calls
`cleanup()` itself, and a suite that needs a process-only API (such as
`process.chdir`) sets `pool: "forks"` in its own config.
