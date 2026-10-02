---
"@angee/app": patch
---

Keep the dev server and Vitest watch mode off Jujutsu's `.jj/` store: `defineAngeeWebViteConfig`, `defineAngeePackageVitestConfig` and `defineAngeeWebVitestConfig` now add `**/.jj/**` to `server.watch.ignored`. Watching `.jj` slowed Vitest startup, timed out `jj` commands and corrupted `working_copy.lock` in jj-managed checkouts.
