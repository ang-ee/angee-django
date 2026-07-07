import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readdirSync, readFileSync, realpathSync, statSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, mergeConfig, type Plugin, type UserConfig } from "vite";

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

// Generated/vendored trees that never feed the prebundle — skipped so an
// unchanged source tree yields a stable signature (no needless re-optimize). Test
// and build artefacts (coverage, the storybook static build, e2e output) churn on
// every run, so hashing them would bust the cache spuriously.
const PREBUNDLE_SKIP_DIRS = new Set([
  "node_modules",
  ".git",
  ".vite",
  ".cache",
  ".turbo",
  "dist",
  "coverage",
  "storybook-static",
  "test-results",
  "playwright-report",
]);

function collectSourceFiles(dir: string, out: string[]): void {
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return; // directory vanished mid-walk (a concurrent build/clean) — nothing to hash
  }
  for (const entry of entries) {
    if (entry.isDirectory()) {
      if (!PREBUNDLE_SKIP_DIRS.has(entry.name)) collectSourceFiles(join(dir, entry.name), out);
    } else if (entry.isFile()) {
      out.push(join(dir, entry.name));
    }
  }
}

// A content signature over the on-disk *source* of the project's `@angee/*`
// packages. Vite derives its optimizer hash from the lockfile + manifests, never
// package source — so a workspace edit to a linked `@angee/*` package (same
// version, same manifest) leaves the prebundle cache valid and Vite serves stale
// code (the slice-1 live-verify trap). Hashing each package's resolved
// (symlink-followed) source paths + mtimes lets a source edit invalidate the
// prebundle while an unchanged, install-stable tree stays cached.
function angeeSourceSignature(webRoot: string, packages: string[]): string {
  const hash = createHash("sha1");
  for (const pkg of packages) {
    let dir: string;
    try {
      dir = realpathSync(join(webRoot, "node_modules", pkg));
    } catch {
      continue; // not installed yet — nothing to hash
    }
    const files: string[] = [];
    collectSourceFiles(dir, files);
    files.sort(); // deterministic, independent of readdir order
    hash.update(pkg);
    for (const file of files) {
      let mtimeMs: number;
      try {
        mtimeMs = statSync(file).mtimeMs;
      } catch {
        continue; // file removed between readdir and stat — skip it
      }
      hash.update(`${file}:${mtimeMs}`);
    }
  }
  return hash.digest("hex");
}

// Whether to force one dependency re-optimize this start: true when the `@angee/*`
// source signature differs from the persisted marker (a workspace source edit).
// Refreshes the marker so the next unchanged start uses the cache again; the
// marker sits beside — not inside — Vite's `deps/` cache, which `force` clears.
// Refreshing the marker is a side effect that must only happen when the force is
// actually consumed — the dev server's optimizer. A `build` never re-optimizes, so
// refreshing there would record the new signature without ever re-bundling, and
// the next `angee dev` would see "unchanged" and serve the stale source (the very
// trap this guards). The caller gates this on `command === "serve"`.
// Exported for unit coverage of the changed-vs-unchanged decision.
export function angeePrebundleForce(webRoot: string, packages: string[]): boolean {
  const marker = join(webRoot, "node_modules", ".vite", "angee-prebundle-source");
  const signature = angeeSourceSignature(webRoot, packages);
  const previous = existsSync(marker) ? readFileSync(marker, "utf8") : "";
  if (signature === previous) return false;
  mkdirSync(dirname(marker), { recursive: true });
  writeFileSync(marker, signature);
  return true;
}

// The dev-server gate for the prebundle force. Vite's `config(config, env)` hook
// runs before the optimizer, once per command, so this is where the serve-only
// `force` (and its marker refresh) belongs — never on `build`, where the force is
// inert and the refresh would swallow a pending change. Exported for unit coverage.
export function angeePrebundleForcePlugin(webRoot: string, packages: string[]): Plugin {
  return {
    name: "angee:prebundle-force",
    config(_config, { command }) {
      if (command !== "serve") return undefined;
      return { optimizeDeps: { force: angeePrebundleForce(webRoot, packages) } };
    },
  };
}

export interface AngeeWebViteConfig extends UserConfig {
  /**
   * Whether Vite pre-bundles this project's `@angee/*` packages. A downstream
   * project consumes them as installed (built) packages and pre-bundles them
   * (`true`); the in-repo example consumes them as linked workspace source and
   * excludes them so HMR serves the framework source (`false`). When `true`, a
   * source signature over the packages busts the prebundle on a workspace edit
   * (`angeePrebundleForce`), so a linked `@angee/*` change is never served stale.
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
    plugins: [
      react(),
      tailwindcss(),
      // Only when this project pre-bundles the linked `@angee/*` source: bust the
      // optimizer cache on a workspace source edit, but only on `serve` (see
      // `angeePrebundleForcePlugin`).
      ...(prebundleAngeePackages ? [angeePrebundleForcePlugin(webRoot, angeePackages)] : []),
    ],
    // The `@angee/gql/<schema>` alias for this project's generated typed
    // operations, pointing at the project's OWN `runtime/gql/<name>/` tree (the
    // web package generates it via codegen). Project-supplied and
    // project-relative — the same resolution the project declares in its
    // tsconfig/vitest.
    resolve: {
      alias: [{ find: /^@angee\/gql\//, replacement: gqlRuntimeDir }],
    },
    // Prebundle installed `@angee/*` packages; the serve-only
    // `angeePrebundleForcePlugin` merges in `force` when their workspace source
    // changed. A project consuming them as linked source excludes them so HMR
    // serves the source directly.
    optimizeDeps: prebundleAngeePackages ? { include: angeePackages } : { exclude: angeePackages },
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
