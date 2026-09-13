import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readdirSync, readFileSync, realpathSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, mergeConfig, type Plugin, type UserConfig } from "vite";
import {
  assertThemeCatalogue,
  resolveThemeOptions,
  THEME_TOKEN_NAMES,
  type ThemeDefinition,
} from "@angee/ui/theme-runtime";

// The framework owner of the web Vite defaults: the plugin pair, the dev-server
// host/port/proxy wiring, the generated-schema alias, and the project-derived
// `optimizeDeps` set. A project's `web/vite.config.ts` imports
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
// generated-schema alias is inlined (two lines) rather than imported from the
// Vitest config module, whose top-level `require.resolve` side effect has no
// place in the Vite build path.

const django = process.env.ANGEE_DJANGO_URL ?? "http://127.0.0.1:8000";
// The operator daemon (the dev-stack supervisor) the console talks to. The
// workspace allocates its port and the stack exports ANGEE_OPERATOR_URL.
const operator = process.env.ANGEE_OPERATOR_URL ?? "http://127.0.0.1:9000";
// The angee workspace allocates a unique UI port and exports it; honour it so a
// workspace's frontend (and the e2e harness targeting it) do not collide on 5173.
const uiPort = Number(process.env.ANGEE_UI_PORT ?? 5173);
// A public Caddy edge proxies its hostname to this dev server; recent Vite
// versions otherwise reject that unknown Host header with a 403. Keep the
// property absent when unset so Vite's own default host policy remains intact.
// Exported for unit coverage of the comma-separated environment seam.
export function angeeUIAllowedHosts(value: string | undefined): string[] | undefined {
  const hosts = (value ?? "")
    .split(",")
    .map((host) => host.trim())
    .filter(Boolean);
  return hosts.length > 0 ? hosts : undefined;
}

const uiAllowedHosts = angeeUIAllowedHosts(process.env.ANGEE_UI_ALLOWED_HOSTS);

// The project's `@angee/*` dependency set, read from the package.json at the
// config cwd (the web package Vite runs in) and sorted for a deterministic
// build. Angee packages expose their root import as either linked TypeScript
// source or built JavaScript. Only the latter is a dependency bundle; linked
// source must remain in Vite's ordinary transform/HMR pipeline.
interface AngeePackageSets {
  all: string[];
  built: string[];
  source: string[];
}

function packageImportEntry(manifest: Record<string, unknown>): string | undefined {
  const exports = manifest.exports;
  const root = typeof exports === "object" && exports !== null
    ? (exports as Record<string, unknown>)["."]
    : exports;
  if (typeof root === "string") return root;
  if (typeof root !== "object" || root === null) return undefined;
  const conditional = root as Record<string, unknown>;
  return typeof conditional.import === "string"
    ? conditional.import
    : typeof conditional.default === "string"
      ? conditional.default
      : undefined;
}

function angeePackagesAt(cwd: string): AngeePackageSets {
  const manifest = JSON.parse(
    readFileSync(join(cwd, "package.json"), "utf8"),
  ) as { dependencies?: Record<string, string> };
  const all = Object.keys(manifest.dependencies ?? {})
    .filter((name) => name.startsWith("@angee/"))
    .sort();
  const built: string[] = [];
  const source: string[] = [];
  for (const name of all) {
    try {
      const packageRoot = realpathSync(join(cwd, "node_modules", name));
      const packageManifest = JSON.parse(
        readFileSync(join(packageRoot, "package.json"), "utf8"),
      ) as Record<string, unknown>;
      const entry = packageImportEntry(packageManifest);
      if (entry && existsSync(join(packageRoot, entry)) && /\.[cm]?js$/.test(entry)) built.push(name);
      else source.push(name);
    } catch {
      // An absent or unfamiliar package cannot be safely forced through the
      // dependency optimizer. Normal resolution will report a useful error.
      source.push(name);
    }
  }
  return { all, built, source };
}

const CODEMIRROR_FAMILY = /^(?:codemirror|@codemirror\/.+|@lezer\/.+)$/;

// CodeMirror keeps its extension registry in module state, so every importer
// has to reach the same module instance. `resolve.dedupe` pins one file on
// disk, but the dependency optimizer can still inline that file into a
// prebundled chunk (a language package, say) while the linked source's own
// imports of the same module are served raw -- two instances, and an editor
// that refuses its extensions. The family that source packages depend on is
// therefore kept out of the optimizer entirely, read from their manifests so a
// new CodeMirror dependency is covered without editing this list.
function servedCodeMirrorFamily(cwd: string, servedPackages: readonly string[]): string[] {
  const family = new Set<string>();
  for (const name of servedPackages) {
    try {
      const packageRoot = realpathSync(join(cwd, "node_modules", name));
      const packageManifest = JSON.parse(
        readFileSync(join(packageRoot, "package.json"), "utf8"),
      ) as { dependencies?: Record<string, string>; peerDependencies?: Record<string, string> };
      for (const dependency of Object.keys({
        ...packageManifest.dependencies,
        ...packageManifest.peerDependencies,
      })) {
        if (CODEMIRROR_FAMILY.test(dependency)) family.add(dependency);
      }
    } catch {
      // An absent package has no dependencies to keep together.
    }
  }
  return [...family].sort();
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
function consumeAngeePrebundleSource(
  webRoot: string,
  packages: string[],
): { changed: boolean; signature: string } {
  const marker = join(webRoot, "node_modules", ".vite", "angee-prebundle-source");
  const signature = angeeSourceSignature(webRoot, packages);
  const previous = existsSync(marker) ? readFileSync(marker, "utf8") : "";
  if (signature !== previous) {
    mkdirSync(dirname(marker), { recursive: true });
    writeFileSync(marker, signature);
  }
  return { changed: signature !== previous, signature };
}

export function angeePrebundleForce(webRoot: string, packages: string[]): boolean {
  return consumeAngeePrebundleSource(webRoot, packages).changed;
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
      const source = consumeAngeePrebundleSource(webRoot, packages);
      return {
        optimizeDeps: {
          force: source.changed,
          // `force` is excluded from Vite's optimizer config hash. Vite does
          // hash the names of native optimizer plugins separately, so this
          // no-op plugin gives linked source a cache identity without changing
          // resolution, transforms, or output.
          rolldownOptions: {
            plugins: [{ name: `angee:prebundle-source:${source.signature}` }],
          },
        },
      };
    },
  };
}

export interface AngeeWebViteConfig extends UserConfig {
  /**
   * Whether Vite pre-bundles this project's built-JavaScript `@angee/*`
   * packages. Linked TypeScript entrypoints always remain source so Vite owns
   * their transforms, asset queries, and HMR. When `true`, a source signature
   * over the included built packages busts the prebundle on a workspace edit.
   */
  prebundleAngeePackages: boolean;
  /**
   * Absolute path to the project's OWN `runtime/gql/` tree, supplied as
   * `fileURLToPath(new URL("../runtime/gql/", import.meta.url))`. Backs the
   * Generated-schema resolve alias — the same target the project declares in
   * its tsconfig and vitest config.
   */
  gqlRuntimeDir: string;
  /**
   * Absolute path to the project's web package. Defaults to `process.cwd()` for
   * direct `vite` usage, but `angee dev` may launch Vite from the repo root.
   */
  webRoot?: string;
  /** Build-owned appearance defaults injected into first paint and the app. */
  appearance?: {
    themeId?: string | null;
    colorScheme?: "light" | "dark" | "system";
    options?: { version: number; value: unknown };
  };
}

export async function defineAngeeWebViteConfig({
  prebundleAngeePackages,
  gqlRuntimeDir,
  webRoot = process.cwd(),
  appearance,
  ...overrides
}: AngeeWebViteConfig): Promise<UserConfig> {
  const angeePackages = angeePackagesAt(webRoot);
  const codeMirrorFamily = servedCodeMirrorFamily(
    webRoot,
    prebundleAngeePackages ? angeePackages.source : angeePackages.all,
  );
  const appearancePayload = await appearanceBuildPayload(gqlRuntimeDir, appearance);
  const base = defineConfig({
    root: webRoot,
    plugins: [
      angeeAppearancePlugin(appearancePayload),
      react(),
      tailwindcss(),
      // Only an actually included built package set needs an optimizer cache
      // signature, consumed on `serve` by `angeePrebundleForcePlugin`.
      ...(prebundleAngeePackages && angeePackages.built.length > 0
        ? [angeePrebundleForcePlugin(webRoot, angeePackages.built)]
        : []),
    ],
    // The alias for this project's generated typed operations, pointing at the
    // project's OWN `runtime/gql/<name>/` tree (the web package generates it via
    // codegen). Project-supplied and
    // project-relative — the same resolution the project declares in its
    // tsconfig/vitest.
    resolve: {
      alias: [{ find: /^@angee\/gql\//, replacement: gqlRuntimeDir }],
      // Context-singleton libraries must resolve to ONE instance even when a
      // linked framework checkout ships its own node_modules beside the
      // project's: an addon file otherwise walks up into the checkout's copy,
      // forking the React context (a second @tanstack/react-router made
      // useNavigate read a null RouterProvider context).
      dedupe: [
        "react",
        "react-dom",
        "@tanstack/react-router",
        "codemirror",
        "@codemirror/state",
        "@codemirror/view",
        "@codemirror/language",
      ],
    },
    // Built package outputs are dependency bundles. Linked TypeScript package
    // entrypoints are application source: leave them in Vite's transform/HMR
    // pipeline so addon asset imports such as `?url` keep their native meaning.
    optimizeDeps: prebundleAngeePackages
      ? { include: angeePackages.built, exclude: [...angeePackages.source, ...codeMirrorFamily] }
      : { exclude: [...angeePackages.all, ...codeMirrorFamily] },
    server: {
      host: true,
      ...(uiAllowedHosts ? { allowedHosts: uiAllowedHosts } : {}),
      port: uiPort,
      strictPort: true,
      proxy: {
        "/graphql/": { target: django, changeOrigin: false, ws: true },
        "/auth/csrf/": { target: django, changeOrigin: false },
        // Curated public-form description + submission endpoint. The React page
        // itself lives at /public/forms/:slug, keeping this exact API prefix
        // unambiguous on hard reloads.
        "/forms/": { target: django, changeOrigin: false },
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

const VIRTUAL_APPEARANCE = "virtual:angee-appearance";
const RESOLVED_VIRTUAL_APPEARANCE = `\0${VIRTUAL_APPEARANCE}`;

interface AppearanceBuildPayload {
  fingerprint: string;
  host: {
    themeId: string | null;
    colorScheme: "light" | "dark" | "system";
    options?: { version: number; value: unknown };
    fingerprint: string;
    tokenLayers: Record<"shared" | "light" | "dark", Record<string, string>>;
  };
  catalogue: {
    schema: number;
    fingerprint: string;
    themes: Array<{ id: string; legacyIds?: readonly string[]; defaultTokens?: Record<string, Record<string, string>> }>;
  };
  tokenNames: readonly string[];
}

interface ThemeCatalogueFile {
  schema: number;
  fingerprint: string;
  themes: Array<{
    id: string;
    legacyIds?: readonly string[];
    optionsVersion?: number | null;
    headlessEntry?: string;
    defaultTokens?: Record<"shared" | "light" | "dark", Record<string, string>>;
  }>;
}

async function appearanceBuildPayload(
  gqlRuntimeDir: string,
  raw: AngeeWebViteConfig["appearance"],
): Promise<AppearanceBuildPayload> {
  const colorScheme = raw?.colorScheme ?? "system";
  if (colorScheme !== "light" && colorScheme !== "dark" && colorScheme !== "system") {
    throw new Error(`Unsupported host appearance color scheme ${JSON.stringify(colorScheme)}.`);
  }
  const catalogPath = join(dirname(gqlRuntimeDir), "web", "themes.catalog.json");
  let sourceCatalogue: ThemeCatalogueFile = { schema: 1, fingerprint: "", themes: [] };
  if (existsSync(catalogPath)) {
    sourceCatalogue = JSON.parse(readFileSync(catalogPath, "utf8")) as ThemeCatalogueFile;
  }
  const requestedThemeId = raw?.themeId ?? null;
  const selectedTheme = requestedThemeId === null
    ? undefined
    : sourceCatalogue.themes.find((theme) => theme.id === requestedThemeId || theme.legacyIds?.includes(requestedThemeId));
  if (requestedThemeId !== null && !selectedTheme) {
    throw new Error(`Host appearance theme ${JSON.stringify(requestedThemeId)} is not installed.`);
  }
  if (raw?.options && requestedThemeId === null) {
    throw new Error("Host appearance options require an installed themeId.");
  }
  if (raw?.options && selectedTheme?.optionsVersion == null) {
    throw new Error(`Host appearance theme ${JSON.stringify(requestedThemeId)} does not accept options.`);
  }
  const themeId = selectedTheme?.id ?? null;

  let normalizedOptions = raw?.options;
  let tokenLayers = selectedTheme?.defaultTokens ?? { shared: {}, light: {}, dark: {} };
  if (raw?.options && selectedTheme) {
    if (typeof selectedTheme.headlessEntry !== "string") {
      throw new Error(`Theme ${JSON.stringify(themeId)} is missing its generated headless entry.`);
    }
    const entry = resolve(dirname(catalogPath), selectedTheme.headlessEntry);
    const themeModule = await importNodeModule(pathToFileURL(entry).href);
    const definitions = assertThemeCatalogue(themeModule.themes as readonly ThemeDefinition<unknown>[]);
    const definition = definitions.find((candidate) => candidate.id === themeId);
    if (!definition) {
      throw new Error(`Theme ${JSON.stringify(themeId)} is missing from ${selectedTheme.headlessEntry}.`);
    }
    const resolvedOptions = resolveThemeOptions(definition, raw.options);
    normalizedOptions = { version: resolvedOptions.version, value: resolvedOptions.value };
    tokenLayers = resolvedOptions.tokens;
  }

  const normalizedHost = {
    themeId,
    colorScheme,
    ...(normalizedOptions ? { options: normalizedOptions } : {}),
    tokenLayers,
  };
  const fingerprint = createHash("sha256")
    .update(sourceCatalogue.fingerprint)
    .update(JSON.stringify(normalizedHost))
    .digest("hex");
  const catalogue: AppearanceBuildPayload["catalogue"] = {
    schema: sourceCatalogue.schema,
    fingerprint: sourceCatalogue.fingerprint,
    themes: sourceCatalogue.themes.map(({ id, legacyIds, defaultTokens }) => ({ id, legacyIds, defaultTokens })),
  };
  return {
    fingerprint,
    host: {
      ...normalizedHost,
      fingerprint,
    },
    catalogue,
    tokenNames: THEME_TOKEN_NAMES,
  };
}

async function importNodeModule(specifier: string): Promise<{ themes?: unknown }> {
  // Vite's config runner rewrites lexical import() calls through its module
  // transport, which may close while an async user config is still resolving.
  // Keep canonical generated file URLs on Node's native ESM loader.
  const load = Function("specifier", "return import(specifier)") as (value: string) => Promise<{ themes?: unknown }>;
  return load(specifier);
}

function angeeAppearancePlugin(payload: AppearanceBuildPayload): Plugin {
  const serialized = JSON.stringify(payload).replaceAll("<", "\\u003c");
  return {
    name: "angee:appearance",
    resolveId(id) {
      return id === VIRTUAL_APPEARANCE ? RESOLVED_VIRTUAL_APPEARANCE : undefined;
    },
    load(id) {
      const { tokenLayers: _tokenLayers, ...appearance } = payload.host;
      return id === RESOLVED_VIRTUAL_APPEARANCE
        ? `export const appearance = ${JSON.stringify(appearance)}; export default appearance;`
        : undefined;
    },
    transformIndexHtml: {
      order: "pre",
      handler() {
        return [{
          tag: "script",
          attrs: { "data-angee-appearance-bootstrap": "" },
          children: appearanceBootstrapScript(serialized),
          injectTo: "head-prepend",
        }];
      },
    },
  };
}

function appearanceBootstrapScript(serializedPayload: string): string {
  return `(()=>{const p=${serializedPayload},r=document.documentElement,n=new Set(p.tokenNames),themes=new Map(p.catalogue.themes.flatMap(t=>[[t.id,t],...(t.legacyIds||[]).map(a=>[a,t])])),safeValue=x=>typeof x==="string"&&x.length>0&&x.length<=256&&!/[;{}@]|url\\s*\\(|expression\\s*\\(|!important/i.test(x),safeTokens=t=>t&&typeof t==="object"&&!Array.isArray(t)&&Object.entries(t).length<=n.size&&Object.entries(t).every(([k,x])=>n.has(k)&&safeValue(x)),hostTokens=s=>({...p.host.tokenLayers.shared,...p.host.tokenLayers[s]});let v={themeId:p.host.themeId,colorSchemePreference:p.host.colorScheme,options:p.host.options,tokens:null};try{const s=localStorage.getItem("angee:appearance");if(s&&s.length<=131072){const c=JSON.parse(s),validTheme=c.themeId===null||typeof c.themeId==="string"&&themes.has(c.themeId),validScheme=c.colorSchemePreference==="light"||c.colorSchemePreference==="dark"||c.colorSchemePreference==="system";if(c.schema===1&&c.fingerprint===p.fingerprint&&validTheme&&validScheme&&(c.tokens==null||safeTokens(c.tokens))){v=c}}}catch{}v={...v,themeId:themes.get(v.themeId)?.id??v.themeId};const q=v.colorSchemePreference||"system",scheme=q==="system"?(matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light"):q,t=v.tokens||(v.themeId===p.host.themeId?hostTokens(scheme):(()=>{const d=themes.get(v.themeId)?.defaultTokens;return d?{...(d.shared||{}),...(d[scheme]||{})}:{}})());if(v.themeId)r.dataset.themeId=v.themeId;else delete r.dataset.themeId;r.dataset.colorScheme=scheme;r.dataset.theme=scheme;r.style.colorScheme=scheme;for(const [k,x] of Object.entries(t||{})){if(n.has(k)&&safeValue(x))r.style.setProperty(k,x)}})();`;
}
