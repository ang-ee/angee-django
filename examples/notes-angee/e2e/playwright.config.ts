import { defineE2EConfig } from "@angee/e2e";

// The whole config: baseURL, the role-auth setup project, reporters, and trace
// policy all come from the framework. See docs/testing/e2e.md.
//
// These specs run against one shared stack (a single Django/GraphQL backend +
// Vite dev server). Unbounded parallelism overwhelms it — the SPA's auth
// bootstrap races under load and transiently redirects to /login — so cap the
// worker count. A single retry absorbs any residual load-induced flake.
export default defineE2EConfig({
  overrides: { workers: 2, retries: 1 },
});
