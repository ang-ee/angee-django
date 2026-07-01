import { readFileSync } from "node:fs";
import { join } from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, mergeConfig, type UserConfig } from "vite";

// The framework owner of the web Vite defaults: the plugin pair, the dev-server
// host/port/proxy wiring, the `@angee/gql/<schema>` alias, and the
// project-derived `optimizeDeps` set. A project's `web/vite.config.ts` imports
// `@angee/app/vite`, calls `defineAngeeWebViteConfig`, and supplies only the two
// project facts the framework cannot know (its prebundle posture and its own
// `runtime/gql/` path), so the proxy map and plugin choices live once, here, and
// never drift across the example and the downstream template. Shipping this in
// `@angee/app` (not a repo-root file) is what lets a project reach it by package
// name whether the framework is an editable checkout or an installed wheel.
//
// This module is loaded by Node as a Vite config and therefore imports ONLY the
// Node-side build plugins and node builtins — never `react`/`react-dom` or any
// `.tsx`, which would pull the browser runtime into the config graph. The
// `@angee/gql` alias is inlined (two lines) rather than imported from
// `@angee/app/vitest`, whose top-level `require.resolve` side effect has no place
// in the Vite build path.

const django = process.env.ANGEE_DJANGO_URL ?? "http://127.0.0.1:8000";
// The operator daemon (the dev-stack supervisor) the console talks to. The
// workspace allocates its port and the stack exports ANGEE_OPERATOR_URL.
const operator = process.env.ANGEE_OPERATOR_URL ?? "http://127.0.0.1:9000";
// The angee workspace allocates a unique UI port and exports it; honour it so a
// workspace's frontend (and the e2e harness targeting it) do not collide on 5173.
const uiPort = Number(process.env.ANGEE_UI_PORT ?? 5173);

// The project's `@angee/*` dependency set, read from the package.json at the
// config cwd (the web package Vite runs in) and sorted for a deterministic
// build. Derived from the manifest so it never drifts as a project adds or drops
// an Angee package — the same drift-free shape the gql alias and tsconfig use.
function angeePackagesAt(cwd: string): string[] {
  const manifest = JSON.parse(
    readFileSync(join(cwd, "package.json"), "utf8"),
  ) as { dependencies?: Record<string, string> };
  return Object.keys(manifest.dependencies ?? {})
    .filter((name) => name.startsWith("@angee/"))
    .sort();
}

export interface AngeeWebViteConfig extends UserConfig {
  /**
   * Whether Vite pre-bundles this project's `@angee/*` packages. A downstream
   * project consumes them as installed (built) packages and pre-bundles them
   * (`true`); the in-repo example consumes them as linked workspace source and
   * excludes them so HMR serves the framework source (`false`).
   */
  prebundleAngeePackages: boolean;
  /**
   * Absolute path to the project's OWN `runtime/gql/` tree, supplied as
   * `fileURLToPath(new URL("../runtime/gql/", import.meta.url))`. Backs the
   * `@angee/gql/<schema>` resolve alias — the same target the project declares
   * in its tsconfig and vitest config.
   */
  gqlRuntimeDir: string;
  /**
   * Absolute path to the project's web package. Defaults to `process.cwd()` for
   * direct `vite` usage, but `angee dev` may launch Vite from the repo root.
   */
  webRoot?: string;
}

export function defineAngeeWebViteConfig({
  prebundleAngeePackages,
  gqlRuntimeDir,
  webRoot = process.cwd(),
  ...overrides
}: AngeeWebViteConfig): UserConfig {
  const angeePackages = angeePackagesAt(webRoot);
  const base = defineConfig({
    root: webRoot,
    plugins: [react(), tailwindcss()],
    // The `@angee/gql/<schema>` alias for this project's generated typed
    // operations, pointing at the project's OWN `runtime/gql/<name>/` tree (the
    // web package generates it via codegen). Project-supplied and
    // project-relative — the same resolution the project declares in its
    // tsconfig/vitest.
    resolve: {
      alias: [{ find: /^@angee\/gql\//, replacement: gqlRuntimeDir }],
    },
    optimizeDeps: prebundleAngeePackages
      ? { include: angeePackages }
      : { exclude: angeePackages },
    server: {
      host: true,
      port: uiPort,
      strictPort: true,
      proxy: {
        "/graphql/": { target: django, changeOrigin: false, ws: true },
        "/auth/csrf/": { target: django, changeOrigin: false },
        // The storage proxy upload/download endpoints are Django REST routes;
        // scope to the exact paths so the SPA's /storage page routes still
        // hard-reload to index.html (a file id is never "upload"/"download").
        "/storage/upload": { target: django, changeOrigin: false },
        "/storage/download": { target: django, changeOrigin: false },
        // Proxy ONLY the daemon GraphQL endpoint (Django sets
        // ANGEE_OPERATOR_GRAPHQL_ENDPOINT=/operator/graphql), stripping the
        // prefix so it lands on the daemon's own /graphql — no cross-origin.
        // Scoped to the exact path so the SPA's own /operator/* page routes
        // still hard-reload to index.html.
        "/operator/graphql": {
          target: operator,
          changeOrigin: false,
          ws: true,
          rewrite: (path) => path.replace(/^\/operator/, ""),
        },
        // The daemon's structured per-service log socket (v0.6). A `^`-anchored
        // regex key proxies only `/operator/services/<name>/logs/stream` (the
        // WebSocket) and strips the prefix to the daemon's own
        // `/services/<name>/logs/stream` — the `/operator/services/<name>` SPA
        // detail route still hard-reloads to index.html.
        "^/operator/services/[^/]+/logs/stream": {
          target: operator,
          changeOrigin: false,
          ws: true,
          rewrite: (path) => path.replace(/^\/operator/, ""),
        },
      },
    },
  });
  return mergeConfig(base, overrides);
}
