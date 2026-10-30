import { mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { runInNewContext } from "node:vm";
import { Window } from "happy-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { build, createServer, resolveConfig, type ViteDevServer } from "vite";
import * as codegen from "@angee/app/web-codegen";
import { defineAngeeWebViteConfig } from "../config/vite";

let root: string;
let runtime: string;
let web: string;
let addon: string;
const servers: ViteDevServer[] = [];

function write(file: string, body: string) {
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, body);
}
function manifest(extra = {}) {
  write(join(runtime, "web", "manifest.json"), JSON.stringify({
    schema: 1,
    addonPackages: [{ package: "@demo/theme", root: "../../linked-addon", sourceRoot: "src/headless" }],
    documentRoots: [{ kind: "package", package: "@demo/theme" }],
    ...extra,
  }));
}
function theme(revision = 1) {
  // Deliberately use plain headless definitions: no browser or framework module
  // can obscure whether Vite refreshes this module's relative-import closure.
  write(join(addon, "src", "themes.mjs"), `
    import { brand } from "./palette.mjs";
    export const themes = [{
      contractVersion: 1, id: "demo.theme", labelKey: "demo.label", descriptionKey: "demo.description",
      revision: ${revision}, stylesheets: ["./theme.css"],
      tokens: { shared: {}, light: { "--brand": brand }, dark: {} },
      options: { version: 1, defaults: {}, parse: (value) => value, resolve: () => ({ light: { "--brand": brand } }) },
    }];
  `);
}
async function config() {
  return defineAngeeWebViteConfig({
    webRoot: web,
    gqlRuntimeDir: join(runtime, "gql"),
    prebundleAngeePackages: false,
    appearance: { themeId: "demo.theme", colorScheme: "light", options: { version: 1, value: {} } },
  });
}
async function start() {
  const server = await createServer({
    ...await config(), configFile: false, logLevel: "silent",
    optimizeDeps: { noDiscovery: true, entries: [] },
    server: { middlewareMode: true, ws: false, watch: { ignored: ["**/.jj/**"] } },
  });
  servers.push(server);
  await vi.waitFor(() => expect(server.watcher.getWatched()[join(addon, "src")]).toBeDefined());
  return server;
}
function catalogue() {
  return JSON.parse(readFileSync(join(runtime, "web", "themes.catalog.json"), "utf8")) as {
    fingerprint: string;
    themes: Array<{ revision: number; defaultTokens: { light: Record<string, string> } }>;
  };
}
async function firstPaintBrand(server: ViteDevServer) {
  const html = await server.transformIndexHtml("/", "<html><head></head><body></body></html>");
  const script = html.match(/<script data-angee-appearance-bootstrap="">([\s\S]*?)<\/script>/)?.[1];
  expect(script).toBeDefined();
  const window = new Window({ url: "http://localhost" });
  try {
    runInNewContext(script!, { document: window.document, localStorage: window.localStorage });
    return window.document.documentElement.style.getPropertyValue("--brand");
  } finally {
    await window.happyDOM.close();
  }
}

beforeEach(() => {
  root = realpathSync(mkdtempSync(join(tmpdir(), "angee-vite-codegen-")));
  runtime = join(root, "runtime");
  web = join(root, "web");
  addon = join(root, "addon");
  write(join(web, "package.json"), '{"dependencies":{}}');
  write(join(web, "index.html"), '<html><head></head><body><script type="module" src="/main.ts"></script></body></html>');
  write(join(web, "main.ts"), 'import appearance from "virtual:angee-appearance"; console.log(appearance);');
  write(join(addon, "package.json"), JSON.stringify({ name: "@demo/theme", exports: { "./themes": "./src/themes.mjs" } }));
  write(join(addon, "src", "headless", "index.ts"), "export default {};");
  write(join(addon, "src", "palette.mjs"), 'export const brand = "#123456";');
  write(join(addon, "src", "theme.css"), '.demo { background-image: url("./mark.svg"); }');
  write(join(addon, "src", "mark.svg"), '<svg xmlns="http://www.w3.org/2000/svg"/>');
  symlinkSync(addon, join(root, "linked-addon"));
  manifest();
  theme();
});
afterEach(async () => {
  await Promise.all(servers.splice(0).map((server) => server.close()));
  vi.restoreAllMocks();
  rmSync(root, { recursive: true, force: true });
});

test("cold build generates its installed theme before validating first-paint appearance", async () => {
  // No generated catalogue exists when Vite starts resolving this configuration.
  const outputs = await build({
    ...await config(), configFile: false, logLevel: "silent", build: { write: false, minify: false },
  });
  expect(catalogue().themes[0]?.defaultTokens.light["--brand"]).toBe("#123456");
  const bundles = Array.isArray(outputs) ? outputs : [outputs];
  const html = bundles.flatMap((bundle) => "output" in bundle ? bundle.output : [])
    .find((entry) => entry.type === "asset" && entry.fileName === "index.html");
  expect(html?.type === "asset" ? html.source : "").toContain("data-angee-appearance-bootstrap");
}, 30_000);

test("preview resolves without any source manifests, catalogues, packages or theme inputs", async () => {
  const value = await config();
  rmSync(runtime, { recursive: true, force: true });
  rmSync(addon, { recursive: true, force: true });
  rmSync(join(web, "package.json"));
  const generate = vi.spyOn(codegen, "generateWebRuntime");
  const resolved = await resolveConfig({ ...value, configFile: false, logLevel: "silent" }, "serve", "production", "production", true);
  expect(resolved.isPreview).toBe(true);
  expect(generate).not.toHaveBeenCalled();
});

test("dev refreshes direct and transitive themes, CSS/assets, first paint and virtual appearance", async () => {
  const server = await start();
  const sent = vi.spyOn(server.ws, "send");
  expect(await firstPaintBrand(server)).toBe("#123456");
  const initialModule = await server.transformRequest("virtual:angee-appearance");
  const initial = catalogue().fingerprint;
  write(join(addon, "src", "palette.mjs"), 'export const brand = "#abcdef";');
  await vi.waitFor(() => expect(catalogue().themes[0]?.defaultTokens.light["--brand"]).toBe("#abcdef"));
  await vi.waitFor(() => expect(sent).toHaveBeenCalledWith({ type: "full-reload" }));
  expect(await firstPaintBrand(server)).toBe("#abcdef");
  expect((await server.transformRequest("virtual:angee-appearance"))?.code).not.toBe(initialModule?.code);
  expect(catalogue().fingerprint).not.toBe(initial);
  theme(2);
  await vi.waitFor(() => expect(catalogue().themes[0]?.revision).toBe(2));
  const beforeCss = catalogue().fingerprint;
  write(join(addon, "src", "theme.css"), '.demo { color: red; background-image: url("./mark.svg"); }');
  await vi.waitFor(() => expect(catalogue().fingerprint).not.toBe(beforeCss));
  const beforeAsset = catalogue().fingerprint;
  write(join(addon, "src", "mark.svg"), '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>');
  await vi.waitFor(() => expect(catalogue().fingerprint).not.toBe(beforeAsset));
}, 30_000);

test("dev watches manifest-owned documents and newly arriving preferred external SDL", async () => {
  const fallback = join(web, "node_modules", "@demo", "schema", "schema.graphql");
  write(fallback, "type Query { ping: String! }");
  manifest({ codegen: [{ schema: "demo", package: "@demo/schema", sdl: "schema.graphql", documents: "documents.demo.ts" }] });
  const server = await start();
  const sent = vi.spyOn(server.ws, "send");
  const document = join(addon, "src", "headless", "documents.demo.ts");
  const generated = join(runtime, "gql", "demo", "graphql.ts");
  write(document, "export const Ping = /* GraphQL */ `query Ping { ping }`;");
  await vi.waitFor(() => expect(readFileSync(generated, "utf8")).toContain("PingDocument"));
  write(document, "export const Again = /* GraphQL */ `query Again { ping }`;");
  await vi.waitFor(() => expect(readFileSync(generated, "utf8")).toContain("AgainDocument"));
  rmSync(document);
  await vi.waitFor(() => expect(readFileSync(generated, "utf8")).not.toContain("AgainDocument"));
  write(join(runtime, "schemas", "external", "demo.graphql"), "type Query { ping: String! pong: String! }");
  write(document, "export const Pong = /* GraphQL */ `query Pong { pong }`;");
  await vi.waitFor(() => expect(readFileSync(generated, "utf8")).toContain("PongDocument"));
  expect(sent.mock.calls.some(([payload]) => typeof payload === "object" && payload.type === "full-reload")).toBe(true);
}, 30_000);

test("dev discovers direct Django SDL and metadata after startup and tracks their changes", async () => {
  const server = await start();
  const sent = vi.spyOn(server.ws, "send");
  const schema = join(runtime, "schemas", "console.graphql");
  const metadata = join(runtime, "schemas", "console.metadata.json");
  const generated = join(runtime, "gql", "console", "graphql.ts");
  write(metadata, "{}\n");
  write(schema, "type Query { ping: String! }");
  await vi.waitFor(() => expect(readFileSync(generated, "utf8")).toContain("ping:"));
  write(schema, "type Query { pong: String! }");
  await vi.waitFor(() => expect(readFileSync(generated, "utf8")).toContain("pong:"));
  await vi.waitFor(() => expect(sent).toHaveBeenCalledWith({ type: "full-reload" }));
  sent.mockClear();
  write(metadata, "{ }\n");
  await vi.waitFor(() => expect(sent).toHaveBeenCalledWith({ type: "full-reload" }));
  rmSync(schema);
  await vi.waitFor(() => expect(readFileSync(join(runtime, "web", "app.ts"), "utf8")).not.toContain('"console":'));
}, 30_000);

test("generation failures show an overlay and a repaired newly declared theme input retries", async () => {
  const server = await start();
  const sent = vi.spyOn(server.ws, "send");
  const manifestPath = join(addon, "package.json");
  write(manifestPath, JSON.stringify({ name: "@demo/theme", exports: { "./themes": "./src/repaired.mjs" } }));
  await vi.waitFor(() => expect(sent.mock.calls.some(([payload]) => typeof payload === "object" && payload.type === "error")).toBe(true));
  sent.mockClear();
  write(join(addon, "src", "repaired.mjs"), readFileSync(join(addon, "src", "themes.mjs"), "utf8"));
  await vi.waitFor(() => expect(sent).toHaveBeenCalledWith({ type: "full-reload" }));
  expect(await firstPaintBrand(server)).toBe("#123456");
}, 30_000);

test("bursts coalesce, concurrent changes get one serial follow-up, outputs stay excluded, and close removes work", async () => {
  const original = codegen.generateWebRuntime;
  let active = 0;
  let maxActive = 0;
  let release: (() => void) | undefined;
  let hold: Promise<void> | undefined;
  const generate = vi.spyOn(codegen, "generateWebRuntime").mockImplementation(async (options) => {
    active += 1;
    maxActive = Math.max(maxActive, active);
    try {
      await hold;
      return await original(options);
    } finally { active -= 1; }
  });
  const server = await start();
  const initialCalls = generate.mock.calls.length;
  const palette = join(addon, "src", "palette.mjs");
  hold = new Promise<void>((resolve) => { release = resolve; });
  for (let index = 0; index < 8; index += 1) server.watcher.emit("all", "change", palette);
  await vi.waitFor(() => expect(generate).toHaveBeenCalledTimes(initialCalls + 1));
  for (let index = 0; index < 8; index += 1) server.watcher.emit("all", "change", palette);
  // Every generated tree and jj churn stays outside the declared source allowlist.
  for (const output of [join(runtime, "gql", "demo", "documents.demo.ts"), join(runtime, "web", "themes.catalog.json"), join(addon, ".jj", "store")]) {
    server.watcher.emit("all", "change", output);
  }
  release!();
  hold = undefined;
  await vi.waitFor(() => expect(generate).toHaveBeenCalledTimes(initialCalls + 2));
  await vi.waitFor(() => expect(active).toBe(0));
  expect(maxActive).toBe(1);
  const beforeClose = generate.mock.calls.length;
  server.watcher.emit("all", "change", palette);
  await server.close();
  expect(generate).toHaveBeenCalledTimes(beforeClose);
  expect(server.watcher.listenerCount("all")).toBe(0);
}, 30_000);
