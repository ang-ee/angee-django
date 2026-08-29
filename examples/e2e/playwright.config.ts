import { defineE2EConfig } from "@angee/e2e";

// The whole config: baseURL, the role-auth setup project, reporters, and trace
// policy all come from the framework. See docs/testing/e2e.md.
//
// These specs run against one shared stack (a single Django/GraphQL backend on
// SQLite + Vite dev server). Concurrent workers race the SPA's auth bootstrap
// (transient redirects to /login) and, worse, contend for the SQLite write lock
// — parallel sign-in/save writes deadlock and wedge the backend. Run the suite
// serially against the shared stack; a single retry absorbs residual flake.
export default defineE2EConfig({
  overrides: { workers: 1, retries: 1 },
});
