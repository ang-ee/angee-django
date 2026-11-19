import { mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { createServer as createHttpServer } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { Window } from "happy-dom";
import { afterEach, beforeEach, expect, onTestFinished, test, vi } from "vitest";
import { build, createServer, preview, resolveConfig, type UserConfig, type ViteDevServer } from "vite";
import * as codegen from "@angee/app/web-codegen";
import { defineAngeeWebViteConfig } from "../config/vite";

let root: string;
let runtime: string;
let web: string;
let addon: string;
const servers: ViteDevServer[] = [];

function write(file: string, body: string | Uint8Array) {
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, body);
}
function manifest(extra = {}, assets: Record<string, string> = {}) {
  write(join(runtime, "web", "manifest.json"), JSON.stringify({
    schema: 1,
    addonPackages: [{ package: "@demo/theme", root: "../../linked-addon", sourceRoot: "src/headless", assets }],
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
async function editSource(server: ViteDevServer, file: string, edit: () => void): Promise<void> {
  let acknowledge!: () => void;
  let timer: ReturnType<typeof setTimeout>;
  const observed = new Promise<void>((resolve, reject) => {
    acknowledge = resolve;
    timer = setTimeout(() => reject(new Error(`No watcher event for ${file}`)), 10_000);
  });
  const onEvent = (event: string, changed: string) => {
    if (["add", "change", "unlink"].includes(event) && changed === file) acknowledge();
  };
  const cleanup = () => { clearTimeout(timer); server.watcher.off("all", onEvent); };
  server.watcher.on("all", onEvent);
  onTestFinished(cleanup);
  try {
    edit();
    await observed;
  } finally {
    cleanup();
  }
}

async function start(allow?: string[]) {
  // Include the probe in the initial scan: creating it between that scan and
  // polling's baseline can leave a new file unobserved until a directory edit.
  const probe = join(addon, "src", "watcher-ready.txt");
  write(probe, "ready");
  const server = await createServer({
    ...await config(), configFile: false, logLevel: "silent",
    optimizeDeps: { noDiscovery: true, entries: [] },
    // Native polling delivers real events in sandboxes without macOS FSEvents.
    server: { middlewareMode: true, ws: false, watch: { usePolling: true, ignored: ["**/.jj/**"] }, ...(allow ? { fs: { allow } } : {}) },
  });
  servers.push(server);
  // A real event, rather than registration alone, acknowledges an active watch.
  let revision = 0;
  const writeProbe = () => write(probe, `ready ${++revision}`);
  const timer = setInterval(writeProbe, 50);
  onTestFinished(() => clearInterval(timer));
  try {
    await editSource(server, probe, writeProbe);
  } finally {
    clearInterval(timer);
  }
  rmSync(probe);
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
  vi.stubEnv("ANGEE_PUBLIC_ORIGIN", undefined);
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
  vi.unstubAllEnvs();
  rmSync(root, { recursive: true, force: true });
});

test.each(["runner", "bundle"] as const)("cold %s build generates its theme before validating first-paint appearance", async (configLoader) => {
  // No generated catalogue exists when Vite starts resolving this configuration.
  const viteConfig = join(web, "vite.config.mts");
  write(viteConfig, `
    import { defineAngeeWebViteConfig } from ${JSON.stringify(fileURLToPath(new URL("../config/vite.ts", import.meta.url)))};
    export default defineAngeeWebViteConfig(${JSON.stringify({
      webRoot: web, gqlRuntimeDir: join(runtime, "gql"), prebundleAngeePackages: false,
      appearance: { themeId: "demo.theme", colorScheme: "light", options: { version: 1, value: {} } },
    })});
  `);
  const outputs = await build({
    configFile: viteConfig, configLoader, logLevel: "silent", build: { write: false, minify: false },
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
  await resolveConfig({ ...value, configFile: false, logLevel: "silent" }, "serve", "production", "production", true);
  expect(generate).not.toHaveBeenCalled();
});

test("dev refreshes direct and transitive themes, CSS/assets, first paint and virtual appearance", async () => {
  const server = await start();
  const sent = vi.spyOn(server.ws, "send");
  expect(await firstPaintBrand(server)).toBe("#123456");
  const initialModule = await server.transformRequest("virtual:angee-appearance");
  const initial = catalogue().fingerprint;
  await editSource(server, join(addon, "src", "palette.mjs"), () => {
    write(join(addon, "src", "palette.mjs"), 'export const brand = "#abcdef";');
  });
  await vi.waitFor(() => expect(catalogue().themes[0]?.defaultTokens.light["--brand"]).toBe("#abcdef"));
  await vi.waitFor(() => expect(sent).toHaveBeenCalledWith({ type: "full-reload" }));
  expect(await firstPaintBrand(server)).toBe("#abcdef");
  expect((await server.transformRequest("virtual:angee-appearance"))?.code).not.toBe(initialModule?.code);
  expect(catalogue().fingerprint).not.toBe(initial);
  await editSource(server, join(addon, "src", "themes.mjs"), () => theme(2));
  await vi.waitFor(() => expect(catalogue().themes[0]?.revision).toBe(2));
  const beforeCss = catalogue().fingerprint;
  await editSource(server, join(addon, "src", "theme.css"), () => {
    write(join(addon, "src", "theme.css"), '.demo { color: red; background-image: url("./mark.svg"); }');
  });
  await vi.waitFor(() => expect(catalogue().fingerprint).not.toBe(beforeCss));
  const beforeAsset = catalogue().fingerprint;
  await editSource(server, join(addon, "src", "mark.svg"), () => {
    write(join(addon, "src", "mark.svg"), '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>');
  });
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
  await editSource(server, document, () => write(document, "export const Ping = /* GraphQL */ `query Ping { ping }`;"));
  await vi.waitFor(() => expect(readFileSync(generated, "utf8")).toContain("PingDocument"));
  await editSource(server, document, () => write(document, "export const Again = /* GraphQL */ `query Again { ping }`;"));
  await vi.waitFor(() => expect(readFileSync(generated, "utf8")).toContain("AgainDocument"));
  await editSource(server, document, () => rmSync(document));
  await vi.waitFor(() => expect(readFileSync(generated, "utf8")).not.toContain("AgainDocument"));
  await editSource(server, document, () => {
    write(join(runtime, "schemas", "external", "demo.graphql"), "type Query { ping: String! pong: String! }");
    write(document, "export const Pong = /* GraphQL */ `query Pong { pong }`;");
  });
  await vi.waitFor(() => expect(readFileSync(generated, "utf8")).toContain("PongDocument"));
  expect(sent).toHaveBeenCalledWith(expect.objectContaining({ type: "full-reload" }));
}, 30_000);

test("dev discovers direct Django SDL and metadata after startup and tracks their changes", async () => {
  const server = await start();
  const sent = vi.spyOn(server.ws, "send");
  const schema = join(runtime, "schemas", "console.graphql");
  const metadata = join(runtime, "schemas", "console.metadata.json");
  const generated = join(runtime, "gql", "console", "graphql.ts");
  await editSource(server, schema, () => {
    write(join(addon, "src", "headless", "documents.ts"), "export const Ping = /* GraphQL */ `query Ping { ping }`;");
    write(metadata, "{}\n");
    write(schema, "type Query { ping: String! }");
  });
  await vi.waitFor(() => expect(readFileSync(generated, "utf8")).toContain("PingDocument"));
  await editSource(server, schema, () => write(schema, "type Query { ping: Int! }"));
  await vi.waitFor(() => expect(readFileSync(generated, "utf8")).toMatch(/type PingQuery = \{\s*ping: number/));
  await vi.waitFor(() => expect(sent).toHaveBeenCalledWith({ type: "full-reload" }));
  sent.mockClear();
  await editSource(server, metadata, () => write(metadata, "{ }\n"));
  await vi.waitFor(() => expect(sent).toHaveBeenCalledWith({ type: "full-reload" }));
  await editSource(server, schema, () => rmSync(schema));
  await vi.waitFor(() => expect(readFileSync(join(runtime, "web", "app.ts"), "utf8")).not.toContain('"console":'));
}, 30_000);

test("generation failures show an overlay and a repaired newly declared theme input retries", async () => {
  const server = await start();
  const sent = vi.spyOn(server.ws, "send");
  const manifestPath = join(addon, "package.json");
  await editSource(server, manifestPath, () => {
    write(manifestPath, JSON.stringify({ name: "@demo/theme", exports: { "./themes": "./src/repaired.mjs" } }));
  });
  await vi.waitFor(() => expect(sent).toHaveBeenCalledWith(expect.objectContaining({ type: "error" })));
  sent.mockClear();
  await editSource(server, join(addon, "src", "repaired.mjs"), () => {
    write(join(addon, "src", "repaired.mjs"), readFileSync(join(addon, "src", "themes.mjs"), "utf8"));
  });
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

const siteTitle = 'Suite <Tools> & "Friends"';
const siteDescription = 'Tools for <everyone> & "you"';
const siteAssets = { "demo.icon": "./art/icon & mark.svg", "demo.image": "./art/tiny preview.png" };
const tinyPng = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3e8AAAAASUVORK5CYII=", "base64");
const iconBytes = '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>';
// Emitted assets are named root-relative, Vite's own convention.
const emittedIcon = "../addon/art/icon & mark.svg";
const emittedImage = "../addon/art/tiny preview.png";

function declareSite(image = "demo.image", assets: Record<string, string> = siteAssets) {
  manifest({ deployment: { shell: {
    brand: { name: "Suite", mark: "suite" },
    site: { title: siteTitle, description: siteDescription, icon: "demo.icon", touchIcon: image, image },
  } } }, assets);
}
function siteFiles() {
  write(join(addon, siteAssets["demo.icon"]), iconBytes);
  write(join(addon, siteAssets["demo.image"]), tinyPng);
  declareSite();
}
function head(html: string) {
  const window = new Window();
  // Inspect static facts independently of the appearance IIFE, tested above.
  const document = new window.DOMParser().parseFromString(html.replace(/<script\b[\s\S]*?<\/script>/gi, ""), "text/html");
  onTestFinished(() => window.happyDOM.close());
  return document.head;
}
function headTags(documentHead: ReturnType<typeof head>) {
  return [...documentHead.querySelectorAll("title,meta,link")].map((tag) => tag.outerHTML);
}
async function builtSite(options: UserConfig = {}) {
  const result = await build({
    ...await config(), configFile: false, logLevel: "silent", build: { write: false }, ...options,
  });
  const outputs = (Array.isArray(result) ? result : [result]).flatMap((bundle) => "output" in bundle ? bundle.output : []);
  const index = outputs.find((entry) => entry.type === "asset" && entry.fileName === "index.html");
  if (!index || index.type !== "asset") throw new Error("Missing built index.html");
  const emitted = (file: string) => {
    const asset = outputs.find((entry) => entry.type === "asset" && entry.originalFileNames.includes(file));
    if (!asset || asset.type !== "asset") throw new Error(`Missing emitted ${file}`);
    return asset;
  };
  return { outputs, emitted, html: Buffer.from(index.source).toString() };
}
/** A real HTTP boundary over a middleware-mode dev server. */
async function listen(server: ViteDevServer) {
  const http = createHttpServer(server.middlewares);
  onTestFinished(() => new Promise<void>((resolve, reject) => http.close((error) => error ? reject(error) : resolve())));
  await new Promise<void>((resolve) => http.listen(0, "127.0.0.1", resolve));
  const address = http.address();
  if (!address || typeof address === "string") throw new Error("Missing dev server address");
  return (url: string) => fetch(`http://127.0.0.1:${address.port}${url}`);
}
/** A dev server without a watcher, transforming a page whose host head holds `tags`. */
async function headServer() {
  const server = await createServer({
    ...await config(), configFile: false, logLevel: "silent",
    optimizeDeps: { noDiscovery: true, entries: [] }, server: { middlewareMode: true, ws: false, watch: null },
  });
  servers.push(server);
  return (tags = "") => server.transformIndexHtml("/", `<html><head>${tags}</head><body></body></html>`);
}

test("dev replaces only the head's title, writes ordered escaped tags, and serves only the selected files", async () => {
  siteFiles();
  const hostAllowed = join(root, "host-allowed");
  write(join(hostAllowed, "kept.txt"), "kept");
  // Inside Vite's default workspace allowance, which the host's list replaces.
  write(join(web, "narrowed.txt"), "narrowed");
  const server = await start([hostAllowed]);
  const body = '<svg><title>Chart</title></svg><script>const label = "<title>Kept</title>";</script>';
  const html = await server.transformIndexHtml("/", `<html><head><title>Angee</title><title>Old duplicate</title></head><body>${body}</body></html>`);
  expect(html).toContain(body);
  const documentHead = head(html);
  expect(documentHead.querySelectorAll("title")).toHaveLength(1);
  expect(documentHead.querySelector("title")?.textContent).toBe(siteTitle);
  expect(html).toContain('Suite &lt;Tools&gt; &amp; "Friends"');
  expect(documentHead.querySelector('meta[name="description"]')?.getAttribute("content")).toBe(siteDescription);
  expect([...documentHead.querySelectorAll("title,meta,link")].map((tag) =>
    tag.tagName === "TITLE" ? "title" : tag.getAttribute("name") ?? tag.getAttribute("property") ?? tag.getAttribute("rel"),
  )).toEqual([
    "title", "description", "og:type", "og:site_name", "og:title", "og:description", "og:image", "twitter:card", "icon", "apple-touch-icon",
  ]);
  expect(documentHead.querySelector('link[rel="icon"]')?.hasAttribute("type")).toBe(false);
  const get = await listen(server);
  for (const [selector, attribute, bytes] of [
    ['link[rel="icon"]', "href", Buffer.from(iconBytes)],
    ['meta[property="og:image"]', "content", tinyPng],
  ] as const) {
    const url = documentHead.querySelector(selector)!.getAttribute(attribute)!;
    expect(url).toMatch(/^\/@fs\//);
    expect(url).not.toMatch(/[ #]/);
    const response = await get(url);
    expect(response.status).toBe(200);
    expect(Buffer.from(await response.arrayBuffer())).toEqual(bytes);
  }
  write(join(addon, "art/unselected.png"), "unselected");
  expect((await get(`/@fs${join(addon, "art/unselected.png")}`)).status).toBe(403);
  expect((await get(`/@fs${join(web, "narrowed.txt")}`)).status).toBe(403);
  expect((await get(`/@fs${join(hostAllowed, "kept.txt")}`)).status).toBe(200);
}, 30_000);

test("dev asset edits reload, missing newly selected files retry, and only this plugin's allowances change", async () => {
  siteFiles();
  const server = await start();
  const get = await listen(server);
  // Another plugin's later allowance survives this plugin's updates.
  const later = join(root, "allowed-later");
  write(join(later, "kept.txt"), "kept");
  server.config.server.fs.allow.push(later);
  const sent = vi.spyOn(server.ws, "send");
  const image = join(addon, siteAssets["demo.image"]);
  expect((await get(encodeURI(`/@fs${image}`))).status).toBe(200);
  await editSource(server, image, () => write(image, "changed bytes"));
  await vi.waitFor(() => expect(sent).toHaveBeenCalledWith({ type: "full-reload" }));
  sent.mockClear();
  await editSource(server, join(runtime, "web/manifest.json"), () => {
    declareSite("demo.repaired", { ...siteAssets, "demo.repaired": "./new-art/repaired.png" });
  });
  await vi.waitFor(() => expect(sent).toHaveBeenCalledWith(expect.objectContaining({ type: "error" })));
  sent.mockClear();
  const repaired = join(addon, "new-art/repaired.png");
  await editSource(server, repaired, () => write(repaired, tinyPng));
  await vi.waitFor(() => expect(sent).toHaveBeenCalledWith({ type: "full-reload" }));
  const html = head(await server.transformIndexHtml("/", "<html><head></head></html>"));
  expect(html.querySelectorAll("title")).toHaveLength(1);
  const repairedUrl = html.querySelector('meta[property="og:image"]')?.getAttribute("content");
  expect(repairedUrl).toBe(encodeURI(`/@fs${repaired}`));
  expect((await get(repairedUrl!)).status).toBe(200);
  expect((await get(encodeURI(`/@fs${image}`))).status).toBe(403);
  expect((await get(`/@fs${join(later, "kept.txt")}`)).status).toBe(200);
}, 30_000);

test("dev keeps a selected file the host's own list allows when regeneration drops it", async () => {
  siteFiles();
  const image = join(addon, siteAssets["demo.image"]);
  const other = join(addon, "art/other.png");
  write(other, tinyPng);
  const server = await start([image]);
  const get = await listen(server);
  const sent = vi.spyOn(server.ws, "send");
  await editSource(server, join(runtime, "web/manifest.json"), () => {
    declareSite("demo.other", { ...siteAssets, "demo.other": "./art/other.png" });
  });
  await vi.waitFor(() => expect(sent).toHaveBeenCalledWith({ type: "full-reload" }));
  expect((await get(encodeURI(`/@fs${other}`))).status).toBe(200);
  expect((await get(encodeURI(`/@fs${image}`))).status).toBe(200);
}, 30_000);

test("dev serves the head's files behind a non-root base on a listening server", async () => {
  siteFiles();
  const server = await createServer({
    ...await config(), configFile: false, logLevel: "silent", base: "/console/",
    optimizeDeps: { noDiscovery: true, entries: [] },
    server: { host: "127.0.0.1", port: 0, ws: false, watch: null },
  });
  servers.push(server);
  await server.listen();
  const address = server.httpServer?.address();
  if (!address || typeof address === "string") throw new Error("Missing dev server address");
  const get = (url: string) => fetch(`http://127.0.0.1:${address.port}${url}`);
  const page = await fetch(`http://127.0.0.1:${address.port}/console/`, { headers: { accept: "text/html" } });
  expect(page.status).toBe(200);
  const icon = head(await page.text()).querySelector('link[rel="icon"]')!.getAttribute("href")!;
  expect(icon).toBe(encodeURI(`/console/@fs${join(addon, siteAssets["demo.icon"])}`));
  const response = await get(icon);
  expect(response.status).toBe(200);
  expect(Buffer.from(await response.arrayBuffer())).toEqual(Buffer.from(iconBytes));
  // Vite's base middleware refuses the unprefixed file route.
  expect((await get(icon.slice("/console".length))).status).toBe(404);
}, 30_000);

test.each([undefined, "https://site.example.test:8443/"])(
  "build emits tiny assets with native root-relative names, base and exact fresh bytes (origin: %s)", async (origin) => {
    vi.stubEnv("ANGEE_PUBLIC_ORIGIN", origin);
    siteFiles();
    write(join(web, "index.html"), '<html><head><title>Angee</title></head><body><script type="module" src="/main.ts"></script></body></html>');
    const options = { base: "/preview/", build: { write: false, manifest: true, rolldownOptions: { output: { assetFileNames: "media/[name]-[hash][extname]" } } } };
    const first = await builtSite(options);
    const documentHead = head(first.html);
    expect(documentHead.querySelectorAll("title")).toHaveLength(1);
    expect(documentHead.querySelector("title")?.textContent).toBe(siteTitle);
    expect(documentHead.querySelector('meta[property="og:url"]')?.getAttribute("content"))
      .toBe(origin ? "https://site.example.test:8443/preview/" : undefined);
    expect(documentHead.querySelector('meta[name="twitter:card"]')?.getAttribute("content")).toBe("summary_large_image");
    // No build-machine path reaches Vite's manifest.
    const viteManifest = first.outputs.find((entry) => entry.fileName === ".vite/manifest.json");
    const manifestSource = viteManifest?.type === "asset" ? Buffer.from(viteManifest.source).toString() : "";
    expect(manifestSource).toContain(JSON.stringify(emittedIcon));
    expect(manifestSource).not.toContain(root);
    const selected = first.outputs.filter((entry) => entry.type === "asset" && entry.originalFileNames.some((file) => file.startsWith("../addon/art/")));
    expect(selected).toHaveLength(2); // image and touch icon select the same file.
    const imageUrl = documentHead.querySelector('meta[property="og:image"]')!.getAttribute("content")!;
    // Rolldown keeps the space in the emitted name; the href encodes it.
    expect(first.emitted(emittedImage).fileName).toContain(" ");
    const imageHref = `/preview/${encodeURI(first.emitted(emittedImage).fileName)}`;
    expect(imageUrl).toBe(origin ? new URL(imageHref, origin).href : imageHref);
    for (const [selector, file, bytes] of [
      ['link[rel="icon"]', emittedIcon, Buffer.from(iconBytes)],
      ['link[rel="apple-touch-icon"]', emittedImage, tinyPng],
    ] as const) {
      const asset = first.emitted(file);
      expect(documentHead.querySelector(selector)?.getAttribute("href")).toBe(`/preview/${encodeURI(asset.fileName)}`);
      expect(Buffer.from(asset.source)).toEqual(bytes);
    }
    const changed = Buffer.concat([tinyPng, Buffer.from("new bytes")]);
    write(join(addon, siteAssets["demo.image"]), changed);
    const next = await builtSite(options);
    const nextImageUrl = head(next.html).querySelector('meta[property="og:image"]')!.getAttribute("content")!;
    expect(nextImageUrl).not.toBe(imageUrl);
    expect(Buffer.from(next.emitted(emittedImage).source)).toEqual(changed);
  }, 30_000,
);

test.each(["./", "https://cdn.example.test/app/"])(
  "build under base %s writes base-prefixed hrefs, and no og:url even with an origin", async (base) => {
    vi.stubEnv("ANGEE_PUBLIC_ORIGIN", "https://site.example.test");
    siteFiles();
    const built = await builtSite({ base });
    const documentHead = head(built.html);
    expect(documentHead.querySelector('meta[property="og:url"]')).toBeNull();
    expect(documentHead.querySelector('link[rel="icon"]')?.getAttribute("href")).toBe(`${base}${encodeURI(built.emitted(emittedIcon).fileName)}`);
    expect(documentHead.querySelector('meta[property="og:image"]')?.getAttribute("content"))
      .toBe(`${base}${encodeURI(built.emitted(emittedImage).fileName)}`);
  }, 30_000,
);

test("no site preserves the built head, and a title alone omits absent facts", async () => {
  const original = '<title>Original &amp; unchanged</title><meta name="description" content="Host">';
  write(join(web, "index.html"), `<html><head>${original}</head><body></body></html>`);
  const built = await builtSite();
  expect(built.html).toContain(original);
  expect(built.html).not.toContain("og:");
  expect(built.html).not.toContain("twitter:");
  write(join(web, "index.html"), "<html><head></head><body></body></html>");
  manifest({ deployment: { shell: { site: { title: "Title alone" } } } });
  const titled = await builtSite();
  expect(titled.html).toContain("<title>Title alone</title>");
  expect(headTags(head(titled.html)), titled.html.replace(/<script\b[\s\S]*?<\/script>/g, "<script>…</script>")).toEqual([
    "<title>Title alone</title>", '<meta property="og:type" content="website">',
    '<meta property="og:title" content="Title alone">', '<meta name="twitter:card" content="summary">',
  ]);
}, 30_000);

test("a brand alone changes only the title; an empty site takes the brand's name for the full head", async () => {
  const brand = { name: "Suite", mark: "suite" };
  write(join(web, "index.html"), '<html><head><title>Angee</title><meta name="description" content="Host"></head><body></body></html>');
  manifest({ deployment: { shell: { brand } } });
  expect(headTags(head((await builtSite()).html))).toEqual(['<meta name="description" content="Host">', "<title>Suite</title>"]);
  write(join(web, "index.html"), "<html><head><title>Angee</title></head><body></body></html>");
  manifest({ deployment: { shell: { brand, site: {} } } });
  expect(headTags(head((await builtSite()).html))).toEqual([
    "<title>Suite</title>", '<meta property="og:type" content="website">', '<meta property="og:site_name" content="Suite">',
    '<meta property="og:title" content="Suite">', '<meta name="twitter:card" content="summary">',
  ]);
}, 30_000);

test("the host head may not also declare a fact the site writes, and the site needs a head", async () => {
  manifest({ deployment: { shell: { site: { title: "Site", description: "Declared" } } } });
  for (const tag of ['<meta name="description" content="Host">', "<meta property='og:locale' content='en'>"]) {
    write(join(web, "index.html"), `<html><head>${tag}</head><body></body></html>`);
    await expect(builtSite()).rejects.toThrow(`declares ${tag} in its head; ANGEE_UI.shell.site owns that tag`);
  }
  // A fact the site does not write stays the host's.
  const hostIcon = '<link rel="icon" href="https://cdn.example.test/host.svg">';
  write(join(web, "index.html"), `<html><head>${hostIcon}</head><body></body></html>`);
  expect(headTags(head((await builtSite()).html))).toContain(hostIcon);
  write(join(web, "index.html"), "<html><body></body></html>");
  await expect(builtSite()).rejects.toThrow("needs a <head> element for the static head");
}, 30_000);

test("a host tag is matched across rel lists, letter case and unquoted values; head tags for other facts pass", async () => {
  siteFiles();
  const transform = await headServer();
  for (const tag of [
    '<link rel="shortcut icon" href="https://cdn.example.test/host.ico">',
    '<META NAME="Description" CONTENT="Host">',
    "<meta name=twitter:card content=summary>",
  ]) {
    await expect(transform(tag)).rejects.toThrow(`declares ${tag} in its head; ANGEE_UI.shell.site owns that tag`);
  }
  const kept = [
    '<meta charset="utf-8">', '<meta name="viewport" content="width=device-width">', '<meta name="robots" content="noindex">',
    '<link rel="manifest" href="https://cdn.example.test/site.webmanifest">', '<link rel="stylesheet" href="https://cdn.example.test/site.css">',
  ];
  expect(headTags(head(await transform(kept.join(""))))).toEqual(expect.arrayContaining(kept));
});

test.each([
  ["https://Site.Example.Test", "https://site.example.test"],
  ["HTTPS://site.example.test/", "https://site.example.test"],
  ["https://site.example.test:443", "https://site.example.test"],
  ["https://bücher.example.test", "https://xn--bcher-kva.example.test"],
  [" https://site.example.test:8443\n", "https://site.example.test:8443"],
])("a public origin Django accepts, %j, writes its normalized form %s", async (value, origin) => {
  vi.stubEnv("ANGEE_PUBLIC_ORIGIN", value);
  manifest({ deployment: { shell: { site: { title: "Site" } } } });
  const html = await (await headServer())();
  expect(head(html).querySelector('meta[property="og:url"]')?.getAttribute("content")).toBe(`${origin}/`);
});

test.each([
  ["site.example.test", "ANGEE_PUBLIC_ORIGIN is not a URL"],
  ["ftp://site.example.test", "origin such as https://example.com, with no user info"],
  ["file:///tmp/site", "origin such as https://example.com, with no user info"],
  ["mailto:u:p@example.test", "origin such as https://example.com, with no user info"],
  ["https://u:p@site.example.test:8443", "origin such as https://site.example.test:8443, with no user info"],
  ["https://site.example.test/p", "origin such as https://site.example.test, with no user info"],
  ["https://site.example.test?x", "origin such as https://site.example.test, with no user info"],
  ["https://site.example.test#f", "origin such as https://site.example.test, with no user info"],
])("config refuses a public origin that is not a bare http(s) origin, %s, without echoing it", async (value, expected) => {
  vi.stubEnv("ANGEE_PUBLIC_ORIGIN", value);
  const message = await resolveConfig({ ...await config(), configFile: false }, "build")
    .then(() => "", (error: Error) => error.message);
  expect(message).toContain(expected);
  expect(message).not.toContain(value);
  expect(message).not.toContain("u:p");
});

test.each([
  ["themes.mjs", "@demo/theme exports ./themes from an invalid or missing path: ./src/themes.mjs"],
  ["palette.mjs", "Headless theme source escapes its package or is missing: "],
  ["theme.css", "Theme demo.theme references missing stylesheet ./theme.css."],
  ["mark.svg", "Theme demo.theme stylesheet asset escapes its package or is missing: ./mark.svg."],
])("theme file %s resolves inside its canonical package, as site assets do", async (name, message) => {
  // The same bytes through a link out of the package: only the containment rule refuses them.
  const outside = join(root, "outside", name);
  write(outside, readFileSync(join(addon, "src", name)));
  rmSync(join(addon, "src", name));
  symlinkSync(outside, join(addon, "src", name));
  await expect(resolveConfig({ ...await config(), configFile: false, logLevel: "silent" }, "build")).rejects.toThrow(message);
});

test("preview serves the built site and assets after sources and runtime disappear", async () => {
  siteFiles();
  const built = await builtSite({ build: { write: true } });
  const value = await config();
  rmSync(runtime, { recursive: true, force: true });
  rmSync(addon, { recursive: true, force: true });
  rmSync(join(web, "package.json"));
  const server = await preview({ ...value, configFile: false, logLevel: "silent", preview: { host: "127.0.0.1", port: 0, strictPort: false } });
  onTestFinished(() => new Promise<void>((resolve, reject) => server.httpServer.close((error) => error ? reject(error) : resolve())));
  const address = server.httpServer.address();
  if (!address || typeof address === "string") throw new Error("Missing preview address");
  const origin = `http://127.0.0.1:${address.port}`;
  expect(await (await fetch(origin)).text()).toBe(built.html);
  const imageUrl = head(built.html).querySelector('meta[property="og:image"]')!.getAttribute("content")!;
  expect(Buffer.from(await (await fetch(new URL(imageUrl, origin))).arrayBuffer())).toEqual(tinyPng);
}, 30_000);
