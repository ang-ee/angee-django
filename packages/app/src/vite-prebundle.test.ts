import { mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, symlinkSync, utimesSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, test } from "vitest";
import { createServer, optimizeDeps, resolveConfig, type ConfigEnv, type Plugin, type UserConfig } from "vite";

import {
  angeePrebundleForce,
  angeePrebundleForcePlugin,
  angeeUIAllowedHosts,
  defineAngeeWebViteConfig,
} from "../config/vite";

// The `config` hook ignores its plugin-context `this`, so drop it for the call.
type ConfigHookFn = (config: UserConfig, env: ConfigEnv) => unknown;

/** Invoke a plugin's `config` hook (function or object form) for one command. */
function runConfigHook(plugin: Plugin, command: "serve" | "build"): UserConfig | undefined {
  const hook = plugin.config;
  const handler = (typeof hook === "function" ? hook : hook?.handler) as ConfigHookFn | undefined;
  const env: ConfigEnv = { command, mode: command === "serve" ? "development" : "production" };
  return handler?.({}, env) as UserConfig | undefined;
}

describe("angeeUIAllowedHosts", () => {
  test("leaves Vite defaults untouched when unset or empty", () => {
    expect(angeeUIAllowedHosts(undefined)).toBeUndefined();
    expect(angeeUIAllowedHosts("")).toBeUndefined();
    expect(angeeUIAllowedHosts(" , ")).toBeUndefined();
  });

  test("parses and trims comma-separated public hostnames", () => {
    expect(angeeUIAllowedHosts("dev.example.com, preview.example.com")).toEqual([
      "dev.example.com",
      "preview.example.com",
    ]);
  });
});

// CodeMirror's package graph: its editor packages and the Lezer parser system.
const CODEMIRROR_IMPORT = /^(?:codemirror|@codemirror\/[^/]+|@lezer\/[^/]+)(?:\/|$)/;
const CODEMIRROR_MODULE = /\/node_modules\/(?:codemirror|@codemirror\/[^/]+|@lezer\/[^/]+)\//;
const uiSource = fileURLToPath(new URL("../../ui/src/", import.meta.url));

/** A web root whose only `@angee/*` dependency is the linked UI source, as on a dev stack. */
function linkedUiWebRoot(prefix: string): string {
  const webRoot = mkdtempSync(join(tmpdir(), prefix));
  writeFileSync(join(webRoot, "package.json"), '{"dependencies":{"@angee/ui":"workspace:*"}}\n');
  symlinkSync(fileURLToPath(new URL("../node_modules", import.meta.url)), join(webRoot, "node_modules"));
  writeFileSync(join(webRoot, "index.html"), '<script type="module" src="/main.ts"></script>\n');
  writeFileSync(join(webRoot, "main.ts"), 'import "@angee/ui"; import "react-dom/client";\n');
  return webRoot;
}

/** Every bare CodeMirror import in the UI's shipped source, with its importing file. */
function uiCodeMirrorImports(): Array<{ specifier: string; importer: string }> {
  return readdirSync(uiSource, { recursive: true, encoding: "utf8" })
    .filter((file) => /\.tsx?$/.test(file) && !/\.(?:test|stories)\./.test(file))
    .sort()
    .flatMap((file) => [...readFileSync(join(uiSource, file), "utf8").matchAll(/\b(?:from|import)\s*\(?\s*"([^"]+)"/g)]
      .flatMap(([, specifier = ""]) => CODEMIRROR_IMPORT.test(specifier) ? [{ specifier, importer: join(uiSource, file) }] : []));
}

// The browser requests linked UI modules one at a time, so whether CodeMirror
// loads once is decided by Vite's request-time resolver after the startup scan.
// Every CodeMirror import of the UI must resolve to the unoptimized file the
// CodeMirror packages import among themselves, and must not register a late
// dependency (a re-optimize and a full reload on first editor use).
test("linked UI requests the CodeMirror graph as one unoptimized copy on first use", async () => {
  const webRoot = linkedUiWebRoot("angee-vite-codemirror-");
  try {
    const config = await defineAngeeWebViteConfig({
      prebundleAngeePackages: true,
      gqlRuntimeDir: join(webRoot, "runtime", "gql"),
      webRoot,
    });
    // An in-process dev environment in middleware mode: no port, socket or watcher.
    const server = await createServer({
      ...config,
      configFile: false,
      plugins: [],
      logLevel: "silent",
      appType: "custom",
      cacheDir: join(webRoot, "cache"),
      server: { middlewareMode: true, ws: false, watch: null },
    });
    try {
      const client = server.environments.client;
      const optimizer = client.depsOptimizer!;
      await optimizer.scanProcessing;
      const requests = uiCodeMirrorImports();
      expect(requests.map(({ specifier }) => specifier)).toEqual(
        expect.arrayContaining(["@codemirror/lang-json", "@codemirror/lang-markdown", "@codemirror/state", "codemirror"]),
      );

      const resolved = new Map<string, string>();
      for (const { specifier, importer } of requests) {
        const id = (await client.pluginContainer.resolveId(specifier, importer))?.id;
        expect(id, `${specifier} from ${importer}`).toBeDefined();
        expect(resolved.get(specifier) ?? id, specifier).toBe(id);
        resolved.set(specifier, id!);
      }

      expect([...resolved].filter(([, id]) => optimizer.isOptimizedDepFile(id))).toEqual([]);
      expect(Object.keys(optimizer.metadata.discovered).filter((id) => CODEMIRROR_IMPORT.test(id))).toEqual([]);
      const graphImporter = resolved.get("codemirror")!.split("?")[0];
      expect((await client.pluginContainer.resolveId("@codemirror/state", graphImporter))?.id)
        .toBe(resolved.get("@codemirror/state"));
    } finally {
      await server.close();
    }
  } finally {
    rmSync(webRoot, { recursive: true, force: true });
  }
}, 30_000);

test.each([false, true])("discovers linked UI dependencies across lazy boundaries at startup (prebundle: %s)", async (prebundleAngeePackages) => {
  const webRoot = linkedUiWebRoot("angee-vite-lazy-");
  try {
    const config = await defineAngeeWebViteConfig({
      prebundleAngeePackages,
      gqlRuntimeDir: join(webRoot, "runtime", "gql"),
      webRoot,
    });
    // Run Vite's native scanner/optimizer without a server. Scanning the UI
    // entry directly supplies the reference graph, including its import()
    // edges, so new lazy dependencies need no hand-maintained test inventory.
    const scanConfig = { ...config, configFile: false as const, plugins: [], logLevel: "silent" as const };
    const reference = await optimizeDeps(await resolveConfig({
      ...scanConfig,
      cacheDir: join(webRoot, "reference-cache"),
      optimizeDeps: { ...config.optimizeDeps, entries: [fileURLToPath(new URL("../../ui/src/index.ts", import.meta.url))] },
    }, "serve"));
    const actual = await optimizeDeps(await resolveConfig({
      ...scanConfig,
      cacheDir: join(webRoot, "actual-cache"),
    }, "serve"));

    expect(Object.keys(reference.optimized)).toContain("@date-fns/tz");
    expect(Object.keys(actual.optimized)).toEqual(expect.arrayContaining(Object.keys(reference.optimized)));
    expect(actual.optimized["react-dom/client"]).toBeDefined();
    expect(actual.optimized["@angee/ui"]).toBeUndefined();
    // No optimized bundle carries a CodeMirror module, as an entry or inlined.
    const deps = join(webRoot, "actual-cache", "deps");
    const bundled = readdirSync(deps)
      .filter((file) => file.endsWith(".map"))
      .flatMap((file) => (JSON.parse(readFileSync(join(deps, file), "utf8")) as { sources: string[] }).sources);
    expect(bundled.length).toBeGreaterThan(0);
    expect(bundled.filter((source) => CODEMIRROR_MODULE.test(source))).toEqual([]);
  } finally {
    rmSync(webRoot, { recursive: true, force: true });
  }
}, 30_000);

test("proxies the agent ACP WebSocket with the browser Origin intact", async () => {
  const webRoot = mkdtempSync(join(tmpdir(), "angee-vite-acp-"));
  try {
    writeFileSync(join(webRoot, "package.json"), '{"dependencies":{}}\n');
    const config = await defineAngeeWebViteConfig({
      prebundleAngeePackages: false,
      gqlRuntimeDir: join(webRoot, "runtime", "gql"),
      webRoot,
    });
    const protocol = readFileSync(
      new URL("../../../addons/angee/agents/protocol.py", import.meta.url), "utf8",
    );
    const acpPath = protocol.match(/^ACP_PATH = "([^"]+)"$/m)?.[1];
    expect(acpPath).toBeDefined();
    expect(config.server?.proxy?.[acpPath!]).toEqual({
      target: (config.server?.proxy?.["/graphql/"] as { target: string }).target,
      changeOrigin: false,
      ws: true,
    });
  } finally {
    rmSync(webRoot, { recursive: true, force: true });
  }
});

test("preloads generated metadata by source path with a custom asset name and base", async () => {
  const webRoot = mkdtempSync(join(tmpdir(), "angee-vite-metadata-"));
  try {
    writeFileSync(join(webRoot, "package.json"), '{"dependencies":{}}\n');
    const config = await defineAngeeWebViteConfig({
      prebundleAngeePackages: false,
      gqlRuntimeDir: join(webRoot, "runtime", "gql"),
      webRoot,
    });
    const plugin = (config.plugins as Plugin[]).find((entry) => entry.name === "angee:schema-metadata-preload");
    expect(plugin?.apply).toBe("build");
    const resolveHook = plugin?.configResolved;
    const setBase = (typeof resolveHook === "function" ? resolveHook : resolveHook?.handler) as
      ((config: { base: string }) => void) | undefined;
    setBase?.({ base: "/preview/" });
    const htmlHook = plugin?.transformIndexHtml;
    const transform = (typeof htmlHook === "function" ? htmlHook : htmlHook?.handler) as
      ((html: string, context: { bundle: Record<string, unknown> }) => unknown) | undefined;
    const tags = transform?.("", { bundle: {
      metadata: { type: "asset", fileName: "data/custom-name.bin", originalFileNames: ["/project/runtime/schemas/public.metadata.json"] },
      decoy: { type: "asset", fileName: "data/console.metadata-abc.json", originalFileNames: ["/project/other.json"] },
    } });
    expect(tags).toEqual([{
      tag: "link",
      attrs: { rel: "preload", as: "fetch", crossorigin: "anonymous", href: "/preview/data/custom-name.bin" },
      injectTo: "head",
    }]);
  } finally {
    rmSync(webRoot, { recursive: true, force: true });
  }
});

// The prebundle cache-bust: `optimizeDeps.force` flips true only when a linked
// `@angee/*` package source changed since the last start, so a workspace edit is
// never served stale while an unchanged, install-stable tree stays cached.
describe("angeePrebundleForce", () => {
  let webRoot: string;
  let source: string;

  beforeEach(() => {
    webRoot = mkdtempSync(join(tmpdir(), "angee-prebundle-"));
    source = join(webRoot, "node_modules", "@angee", "ui", "src", "index.ts");
    mkdirSync(join(webRoot, "node_modules", "@angee", "ui", "src"), { recursive: true });
    writeFileSync(source, "export const x = 1;\n");
  });

  afterEach(() => {
    rmSync(webRoot, { recursive: true, force: true });
  });

  test("forces on first run, then stays cached until the source changes", () => {
    // First start: no marker yet → optimize (and persist the signature).
    expect(angeePrebundleForce(webRoot, ["@angee/ui"])).toBe(true);
    // Unchanged tree → cached, no needless re-optimize.
    expect(angeePrebundleForce(webRoot, ["@angee/ui"])).toBe(false);

    // A workspace source edit (later mtime) → bust once, then cache again.
    const later = Date.now() / 1000 + 10;
    utimesSync(source, later, later);
    expect(angeePrebundleForce(webRoot, ["@angee/ui"])).toBe(true);
    expect(angeePrebundleForce(webRoot, ["@angee/ui"])).toBe(false);
  });

  test("an uninstalled package contributes nothing and does not throw", () => {
    // Absent `node_modules/@angee/missing` is skipped, not fatal; with only the
    // present package unchanged, the second start stays cached.
    expect(angeePrebundleForce(webRoot, ["@angee/ui", "@angee/missing"])).toBe(true);
    expect(angeePrebundleForce(webRoot, ["@angee/ui", "@angee/missing"])).toBe(false);
  });

  test("churny build/test artefact dirs do not bust the cache", () => {
    // First start records the signature.
    expect(angeePrebundleForce(webRoot, ["@angee/ui"])).toBe(true);
    // A fresh coverage/test-results tree inside the package must be skipped, so
    // the unchanged source tree still reads as cached.
    for (const dir of ["coverage", "storybook-static", "test-results", "playwright-report"]) {
      const churn = join(webRoot, "node_modules", "@angee", "ui", dir);
      mkdirSync(churn, { recursive: true });
      writeFileSync(join(churn, "report.txt"), `${Math.random()}\n`);
    }
    expect(angeePrebundleForce(webRoot, ["@angee/ui"])).toBe(false);
  });
});

// The force is only consumed by the dev server's optimizer, so the marker refresh
// must be gated on `command === "serve"` — a `build` in between must never swallow
// a pending change and leave the next `angee dev` serving stale source.
describe("angeePrebundleForcePlugin", () => {
  let webRoot: string;
  let source: string;

  beforeEach(() => {
    webRoot = mkdtempSync(join(tmpdir(), "angee-prebundle-plugin-"));
    source = join(webRoot, "node_modules", "@angee", "ui", "src", "index.ts");
    mkdirSync(join(webRoot, "node_modules", "@angee", "ui", "src"), { recursive: true });
    writeFileSync(source, "export const x = 1;\n");
  });

  afterEach(() => {
    rmSync(webRoot, { recursive: true, force: true });
  });

  test("only serve consumes the force; a build never refreshes the marker", () => {
    const plugin = angeePrebundleForcePlugin(webRoot, ["@angee/ui"]);

    // A build must not touch the marker: it contributes no config and leaves any
    // pending change unconsumed.
    expect(runConfigHook(plugin, "build")).toBeUndefined();

    // First serve sees the change (no marker yet) → force, and records it.
    const first = runConfigHook(plugin, "serve");
    expect(first).toMatchObject({ optimizeDeps: { force: true } });
    // Unchanged tree → cached.
    const unchanged = runConfigHook(plugin, "serve");
    expect(unchanged).toMatchObject({ optimizeDeps: { force: false } });
    expect(unchanged).toEqual({
      optimizeDeps: {
        force: false,
        rolldownOptions: first?.optimizeDeps?.rolldownOptions,
      },
    });

    // Edit the source; a build in between must NOT swallow it — the next serve
    // still forces the re-optimize.
    const later = Date.now() / 1000 + 10;
    utimesSync(source, later, later);
    expect(runConfigHook(plugin, "build")).toBeUndefined();
    const changed = runConfigHook(plugin, "serve");
    expect(changed).toMatchObject({ optimizeDeps: { force: true } });
    expect(changed?.optimizeDeps?.rolldownOptions).not.toEqual(
      first?.optimizeDeps?.rolldownOptions,
    );
  });
});
