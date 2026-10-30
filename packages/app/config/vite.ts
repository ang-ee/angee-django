import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readdirSync, readFileSync, realpathSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, matchesGlob, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { build, defineConfig, mergeConfig, type Plugin, type UserConfig, type ViteDevServer } from "vite";
import { generateWebRuntime, type WebCodegenInputs } from "@angee/app/web-codegen";
import {
  resolveThemeOptions,
  type AppearanceBootstrapPayload,
  type ThemeDefinition,
} from "@angee/ui/theme-runtime";

// Never watch Jujutsu's `.jj/` store: a watcher there slows Vitest startup, times
// out `jj` commands, and corrupts `working_copy.lock` (see the jj FAQ). Vite
// already ignores `.git/` and `node_modules/`. Kept inline here and in `./vitest.ts`:
// these config modules load through Node's own ESM loader in consumers, which
// does not resolve extensionless relative imports.
const ANGEE_WATCH_IGNORED: readonly string[] = ["**/.jj/**"];

// The framework owner of the web Vite defaults: the plugin pair, the dev-server
// host/port/proxy wiring, the generated-schema alias, and the project-derived
// `optimizeDeps` set. A project's `web/vite.config.ts` imports
// `@angee/app/vite`, calls `defineAngeeWebViteConfig`, and supplies only the two
// project facts the framework cannot know (its prebundle posture and its own
// `runtime/gql/` path), so the proxy map and plugin choices live once, here, and
// never drift across the example and the downstream template. Shipping this in
// `@angee/app` (not a repo-root file) is what lets a project reach it by package
// name from either a source checkout or a published JavaScript package.
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
  entries: string[];
  codemirror: string[];
}

// CodeMirror extensions are branded by the @codemirror/state instance that
// created them, as Lezer node props and highlight tags are by their module, so
// the CodeMirror graph must load as one unoptimized copy from node_modules.
// Vite's request-time discovery matches `optimizeDeps.exclude` by exact package
// name; a scope prefix reaches only its scanner and bundler, so "@codemirror"
// let a first JSON or markdown editor optimize its language package (with
// private Lezer copies) and reload the page. Every CodeMirror package an
// `@angee/*` package declares is therefore excluded by name; their own imports
// come from node_modules, which Vite never optimizes on request.
const CODEMIRROR_PACKAGE = /^(?:codemirror$|@codemirror\/|@lezer\/)/;

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
  const entries: string[] = [];
  const codemirror = new Set<string>();
  for (const name of all) {
    try {
      const packageRoot = realpathSync(join(cwd, "node_modules", name));
      const packageManifest = JSON.parse(
        readFileSync(join(packageRoot, "package.json"), "utf8"),
      ) as Record<string, unknown> & {
        dependencies?: Record<string, string>;
        peerDependencies?: Record<string, string>;
      };
      for (const dependency of Object.keys({ ...packageManifest.dependencies, ...packageManifest.peerDependencies })) {
        if (CODEMIRROR_PACKAGE.test(dependency)) codemirror.add(dependency);
      }
      const entry = packageImportEntry(packageManifest);
      if (entry && existsSync(join(packageRoot, entry))) {
        entries.push(join(packageRoot, entry));
        if (/\.[cm]?js$/.test(entry)) built.push(name);
        else source.push(name);
      } else source.push(name);
    } catch {
      // An absent or unfamiliar package cannot be safely forced through the
      // dependency optimizer. Normal resolution will report a useful error.
      source.push(name);
    }
  }
  return { all, built, source, entries, codemirror: [...codemirror].sort() };
}

// Generated/vendored trees that never feed the prebundle — skipped so an
// unchanged source tree yields a stable signature (no needless re-optimize). Test
// and build artefacts (coverage, the storybook static build, e2e output) churn on
// every run, so hashing them would bust the cache spuriously.
const PREBUNDLE_SKIP_DIRS = new Set([
  "node_modules",
  ".git",
  ".jj",
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
    config(_config, { command, isPreview }) {
      if (command !== "serve" || isPreview) return undefined;
      return { optimizeDeps: angeePrebundleOptimizerConfig(webRoot, packages) };
    },
  };
}

function angeePrebundleOptimizerConfig(webRoot: string, packages: string[]) {
  const source = consumeAngeePrebundleSource(webRoot, packages);
  return {
    force: source.changed,
    // Vite excludes force from the optimizer hash but hashes plugin names.
    rolldownOptions: { plugins: [{ name: `angee:prebundle-source:${source.signature}` }] },
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
  const appearancePlugin = angeeAppearancePlugin();
  const base = defineConfig({
    root: webRoot,
    plugins: [
      angeeWebCodegenPlugin({ webRoot, gqlRuntimeDir, appearance, appearancePlugin, prebundleAngeePackages }),
      appearancePlugin,
      preloadSchemaMetadata(),
      react(),
      tailwindcss(),
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
    server: {
      host: true,
      ...(uiAllowedHosts ? { allowedHosts: uiAllowedHosts } : {}),
      watch: { ignored: [...ANGEE_WATCH_IGNORED] },
      port: uiPort,
      strictPort: true,
      proxy: {
        "/graphql/": { target: django, changeOrigin: false, ws: true },
        "/acp/agents/": { target: django, changeOrigin: false, ws: true },
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

/** Vite owns generation scheduling, input watches, errors and module invalidation. */
function angeeWebCodegenPlugin({
  webRoot,
  gqlRuntimeDir,
  appearance,
  appearancePlugin,
  prebundleAngeePackages,
}: Pick<AngeeWebViteConfig, "webRoot" | "gqlRuntimeDir" | "appearance" | "prebundleAngeePackages"> & {
  webRoot: string;
  appearancePlugin: ReturnType<typeof angeeAppearancePlugin>;
}): Plugin {
  const runtimeDir = dirname(gqlRuntimeDir);
  let inputs: WebCodegenInputs = { files: [], globs: [], directories: [], excluded: [] };
  let discovering = inputs;
  let server: ViteDevServer | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let running: Promise<void> | undefined;
  let dirty = false;
  let closed = false;
  const watched = new Set<string>();
  const watchInputs = (next: WebCodegenInputs) => {
    discovering = next;
    if (!server) return;
    const added = next.directories.filter((directory) => !watched.has(directory));
    for (const directory of added) watched.add(directory);
    // Vite deliberately ignores actual node_modules. Linked authored packages
    // arrive as canonical source directories; installed dependency changes use
    // the operator dependency restart and the next cold generation pass.
    server.watcher.add(added);
  };
  const generate = async () => {
    // Invalidation and relative-import loading belong to Vite's SSR module graph.
    // Native import() is used only before a server exists (CLI/startup/build).
    server?.environments.ssr?.moduleGraph.invalidateAll();
    const generated = await generateWebRuntime({
      webRoot,
      runtimeDir,
      onInputs: watchInputs,
      ...(server ? { loadThemeModule: (entry: string) => server!.ssrLoadModule(entry) } : {}),
    });
    const payload = appearanceBuildPayload(gqlRuntimeDir, appearance, generated.themes);
    inputs = generated.inputs;
    discovering = inputs;
    appearancePlugin.update(payload);
    const module = server?.environments.client.moduleGraph.getModuleById(RESOLVED_VIRTUAL_APPEARANCE);
    if (module) server!.environments.client.moduleGraph.invalidateModule(module);
  };
  const regenerate = () => {
    if (closed || running) return;
    running = (async () => {
      do {
        dirty = false;
        try {
          await generate();
          if (!closed) server?.ws.send({ type: "full-reload" });
        } catch (error) {
          if (!closed && server) {
            const failure = error instanceof Error ? error : new Error(String(error));
            server.config.logger.error(`[angee:web-codegen] ${failure.stack ?? failure.message}`);
            server.ws.send({ type: "error", err: {
              message: failure.message, stack: failure.stack ?? "", plugin: "angee:web-codegen",
            } });
          }
        }
      } while (dirty && !closed);
    })().finally(() => { running = undefined; });
  };
  const changed = (event: string, file: string) => {
    if (closed || !["add", "change", "unlink"].includes(event)) return;
    const absolute = resolve(file).replaceAll("\\", "/");
    if (absolute.split("/").some((part) => part === ".jj" || part === ".git")) return;
    const matches = (declared: WebCodegenInputs) => !declared.excluded.some((glob) => matchesGlob(absolute, glob))
      && (declared.files.includes(absolute) || declared.globs.some((glob) => matchesGlob(absolute, glob)));
    if (!matches(inputs) && !matches(discovering)) return;
    dirty = true;
    if (!running) {
      clearTimeout(timer);
      timer = setTimeout(regenerate, 25);
    }
  };
  return {
    name: "angee:web-codegen",
    enforce: "pre",
    async config(config, env) {
      // Preview serves the completed distribution and must work without runtime
      // manifests, source themes, schemas or even a source package.json.
      if (env.isPreview) return;
      await generate();
      const packages = angeePackagesAt(webRoot);
      const prebundle = prebundleAngeePackages && packages.built.length > 0 && env.command === "serve"
        ? angeePrebundleOptimizerConfig(webRoot, packages.built)
        : {};
      const defaults = { optimizeDeps: {
        // Linked source stays in Vite's native transform/HMR graph; scanning its
        // entries discovers dependencies across lazy import boundaries.
        entries: packages.entries.length > 0 ? ["**/*.html", ...packages.entries] : undefined,
        include: prebundleAngeePackages ? packages.built : undefined,
        exclude: [...(prebundleAngeePackages ? packages.source : packages.all), ...packages.codemirror],
        ...prebundle,
      } };
      // Native config hooks may mutate the current config. Merge once in the
      // same order as the public factory: defaults first, host overrides last.
      config.optimizeDeps = mergeConfig(defaults, { optimizeDeps: config.optimizeDeps }).optimizeDeps;
    },
    configureServer(value) {
      server = value;
      watchInputs(inputs);
      server.watcher.on("all", changed);
    },
    async closeBundle() {
      closed = true;
      clearTimeout(timer);
      server?.watcher.off("all", changed);
      await running;
    },
  };
}

// Generated metadata is fetched as data, so start its download with the entry
// script. Match the source path, not Vite's configurable output asset name.
function preloadSchemaMetadata(): Plugin {
  let base = "/";
  return {
    name: "angee:schema-metadata-preload",
    apply: "build",
    configResolved(config) {
      base = config.base;
    },
    transformIndexHtml: {
      order: "post",
      handler(_html, context) {
        return Object.values(context.bundle ?? {})
          .filter((asset) => asset.type === "asset" && asset.originalFileNames?.some((source) =>
            /(^|\/)runtime\/schemas\/[^/]+\.metadata\.json$/.test(source.replaceAll("\\", "/"))))
          .sort((left, right) => left.fileName.localeCompare(right.fileName))
          .map((asset) => ({
            tag: "link",
            attrs: {
              rel: "preload",
              as: "fetch",
              crossorigin: "anonymous",
              href: `${base}${asset.fileName}`,
            },
            injectTo: "head" as const,
          }));
      },
    },
  };
}

const VIRTUAL_APPEARANCE = "virtual:angee-appearance";
const RESOLVED_VIRTUAL_APPEARANCE = `\0${VIRTUAL_APPEARANCE}`;

interface ThemeCatalogueFile {
  schema: number;
  fingerprint: string;
  themes: Array<{
    id: string;
    optionsVersion?: number | null;
    defaultTokens?: Record<"shared" | "light" | "dark", Record<string, string>>;
  }>;
}

function appearanceBuildPayload(
  gqlRuntimeDir: string,
  raw: AngeeWebViteConfig["appearance"],
  definitions: readonly ThemeDefinition<unknown>[],
): AppearanceBootstrapPayload {
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
    : sourceCatalogue.themes.find((theme) => theme.id === requestedThemeId);
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
    const definition = definitions.find((candidate) => candidate.id === themeId);
    if (!definition) throw new Error(`Theme ${JSON.stringify(themeId)} is missing its generated definition.`);
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
  const catalogue: AppearanceBootstrapPayload["catalogue"] = {
    schema: sourceCatalogue.schema,
    fingerprint: sourceCatalogue.fingerprint,
    themes: sourceCatalogue.themes.map(({ id, defaultTokens }) => ({ id, defaultTokens })),
  };
  return {
    fingerprint,
    host: {
      ...normalizedHost,
      fingerprint,
    },
    catalogue,
  };
}

function angeeAppearancePlugin(): Plugin & { update(payload: AppearanceBootstrapPayload): void } {
  let payload: AppearanceBootstrapPayload | undefined;
  let bootstrap: Promise<string> | undefined;
  return {
    name: "angee:appearance",
    update(next) {
      payload = next;
      bootstrap = undefined;
    },
    resolveId(id) {
      return id === VIRTUAL_APPEARANCE ? RESOLVED_VIRTUAL_APPEARANCE : undefined;
    },
    load(id) {
      if (id !== RESOLVED_VIRTUAL_APPEARANCE || !payload) return;
      const { tokenLayers: _tokenLayers, ...appearance } = payload.host;
      return `export const appearance = ${JSON.stringify(appearance)}; export default appearance;`;
    },
    transformIndexHtml: {
      order: "pre",
      async handler() {
        if (!payload) return;
        bootstrap ??= appearanceBootstrapScript(payload);
        return [{
          tag: "script",
          attrs: { "data-angee-appearance-bootstrap": "" },
          children: await bootstrap,
          injectTo: "head-prepend",
        }];
      },
    },
  };
}

async function appearanceBootstrapScript(payload: AppearanceBootstrapPayload): Promise<string> {
  // Library entries are normalized to absolute paths before plugin resolution.
  const entry = fileURLToPath(new URL("./appearance-bootstrap.virtual.js", import.meta.url));
  const runtime = fileURLToPath(import.meta.resolve("@angee/ui/theme-runtime"));
  // Vite owns dependency closure and minification; no second token validator or
  // function-source serializer lives in app configuration. Write nothing to disk.
  const result = await build({
    configFile: false,
    envFile: false,
    publicDir: false,
    logLevel: "silent",
    plugins: [{
      name: "angee:appearance-entry",
      resolveId(id) { return id === entry ? entry : undefined; },
      load(id) {
        return id === entry
          ? `import { bootstrapAppearance } from ${JSON.stringify(runtime)}; bootstrapAppearance(${JSON.stringify(payload)});`
          : undefined;
      },
    }],
    build: {
      write: false,
      minify: true,
      lib: { entry, name: "AngeeAppearanceBootstrap", fileName: "appearance-bootstrap", formats: ["iife"] },
    },
  });
  const outputs = Array.isArray(result) ? result : [result];
  const chunks = outputs.flatMap((output) => "output" in output ? output.output : []);
  if (chunks.length !== 1 || chunks[0]?.type !== "chunk") {
    throw new Error("Appearance bootstrap must compile to one synchronous script.");
  }
  // Keep HTML's script parser from interpreting a closing tag inside a JS string.
  return chunks[0].code.replace(/<\/script/gi, "<\\/script");
}
