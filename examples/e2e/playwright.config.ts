import { defineE2EConfig } from "@angee/e2e";

// BaseURL, the role-auth setup project and reporters come from the framework.
// See docs/testing/e2e.md.
//
// These specs run against one shared stack (a single Django/GraphQL backend on
// SQLite + Vite dev server). Concurrent workers race the SPA's auth bootstrap
// (transient redirects to /login) and, worse, contend for the SQLite write lock
// — parallel sign-in/save writes deadlock and wedge the backend. Run the suite
// serially against the shared stack. Ledger-driven reads can take more than
// five seconds under its concurrent GraphQL load; await the rendered state
// within an explicit budget instead of relying on retries.
export default defineE2EConfig({
  overrides: {
    workers: 1,
    retries: 0,
    timeout: 60_000,
    expect: { timeout: 15_000 },
    use: { trace: "retain-on-failure" },
  },
});
