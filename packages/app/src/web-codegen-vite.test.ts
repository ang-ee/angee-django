import { mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { createServer as createHttpServer } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { Window } from "happy-dom";
import { afterEach, beforeEach, expect, onTestFinished, test, vi } from "vitest";
import { build, createServer, preview, resolveConfig, searchForWorkspaceRoot, type UserConfig, type ViteDevServer } from "vite";
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
async function builtSite(options: UserConfig = {}) {
  const result = await build({
    ...await config(), configFile: false, logLevel: "silent", build: { write: false }, ...options,
  });
  const outputs = (Array.isArray(result) ? result : [result]).flatMap((bundle) => "output" in bundle ? bundle.output : []);
  const index = outputs.find((entry) => entry.type === "asset" && entry.fileName === "index.html");
  if (!index || index.type !== "asset") throw new Error("Missing built index.html");
  return { outputs, html: Buffer.from(index.source).toString() };
}

test("dev replaces the title, writes ordered escaped tags, and serves only the selected files beyond its allowances", async () => {
  siteFiles();
  const hostAllowed = join(root, "host-allowed");
  const server = await start([hostAllowed]);
  const html = await server.transformIndexHtml("/", "<html><head><title>Angee</title><title>Old duplicate</title></head><body></body></html>");
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
  expect(documentHead.querySelector('link[rel="icon"]')?.getAttribute("type")).toBe("image/svg+xml");
  const selected = Object.values(siteAssets).map((file) => join(addon, file));
  expect(server.config.server.fs.allow).toEqual(expect.arrayContaining([searchForWorkspaceRoot(web), hostAllowed, ...selected]));
  expect(server.config.server.fs.allow).not.toContain(addon);
  expect(server.config.server.fs.allow).not.toContain(join(addon, "art"));
  const http = createHttpServer(server.middlewares);
  onTestFinished(() => new Promise<void>((resolve, reject) => http.close((error) => error ? reject(error) : resolve())));
  await new Promise<void>((resolve) => http.listen(0, "127.0.0.1", resolve));
  const address = http.address();
  if (!address || typeof address === "string") throw new Error("Missing dev server address");
  const get = (url: string) => fetch(`http://127.0.0.1:${address.port}${url}`);
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
  expect((await get(`/@fs/${join(addon, "art/unselected.png")}`)).status).toBe(403);
}, 30_000);

test("dev asset edits reload, missing newly selected files retry, and obsolete file allowances disappear", async () => {
  siteFiles();
  const server = await start();
  const sent = vi.spyOn(server.ws, "send");
  const image = join(addon, siteAssets["demo.image"]);
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
  expect(html.querySelector('meta[property="og:image"]')?.getAttribute("content")).toContain("new-art/repaired.png");
  expect(server.config.server.fs.allow).toContain(repaired);
  expect(server.config.server.fs.allow).not.toContain(image);
}, 30_000);

test.each([undefined, "https://user:secret@site.example.test:8443/path?ignored=yes#fragment"])(
  "build emits tiny assets with native names, base and exact fresh bytes (origin: %s)", async (origin) => {
    vi.stubEnv("ANGEE_PUBLIC_ORIGIN", origin);
    siteFiles();
    write(join(web, "index.html"), '<html><head><title>Angee</title></head><body><script type="module" src="/main.ts"></script></body></html>');
    const options = { base: "/preview/", build: { write: false, rolldownOptions: { output: { assetFileNames: "media/[name]-[hash][extname]" } } } };
    const first = await builtSite(options);
    const documentHead = head(first.html);
    expect(documentHead.querySelectorAll("title")).toHaveLength(1);
    expect(documentHead.querySelector("title")?.textContent).toBe(siteTitle);
    expect(documentHead.querySelector('meta[property="og:url"]')?.getAttribute("content"))
      .toBe(origin ? "https://site.example.test:8443/preview/" : undefined);
    expect(documentHead.querySelector('meta[name="twitter:card"]')?.getAttribute("content")).toBe("summary_large_image");
    const selected = first.outputs.filter((entry) => entry.type === "asset" && entry.originalFileNames.some((file) => file.startsWith(join(addon, "art"))));
    expect(selected).toHaveLength(2); // image and touch icon select the same file.
    const imageUrl = documentHead.querySelector('meta[property="og:image"]')!.getAttribute("content")!;
    expect(imageUrl).toMatch(origin ? /^https:\/\/site.example.test:8443\/preview\/media\// : /^\/preview\/media\//);
    for (const [selector, attribute, bytes] of [
      ['link[rel="icon"]', "href", Buffer.from(iconBytes)],
      ['meta[property="og:image"]', "content", tinyPng],
      ['link[rel="apple-touch-icon"]', "href", tinyPng],
    ] as const) {
      const url = documentHead.querySelector(selector)!.getAttribute(attribute)!;
      if (attribute === "href") expect(url).toMatch(/^\/preview\/media\//);
      const fileName = decodeURIComponent(new URL(url, "http://localhost").pathname).replace("/preview/", "");
      const emitted = first.outputs.find((entry) => entry.fileName === fileName);
      expect(emitted?.type === "asset" ? Buffer.from(emitted.source) : undefined).toEqual(bytes);
    }
    const changed = Buffer.concat([tinyPng, Buffer.from("new bytes")]);
    write(join(addon, siteAssets["demo.image"]), changed);
    const next = await builtSite(options);
    const nextImageUrl = head(next.html).querySelector('meta[property="og:image"]')!.getAttribute("content")!;
    expect(nextImageUrl).not.toBe(imageUrl);
    const emitted = next.outputs.find((entry) => entry.fileName === decodeURIComponent(new URL(nextImageUrl, "http://localhost").pathname).replace("/preview/", ""));
    expect(emitted?.type === "asset" ? Buffer.from(emitted.source) : undefined).toEqual(changed);
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
  const documentHead = head(titled.html);
  expect([...documentHead.querySelectorAll("title,meta,link")].map((tag) => tag.outerHTML),
    titled.html.replace(/<script\b[\s\S]*?<\/script>/g, "<script>…</script>"),
  ).toEqual([
    "<title>Title alone</title>", '<meta property="og:type" content="website">',
    '<meta property="og:title" content="Title alone">', '<meta name="twitter:card" content="summary">',
  ]);
}, 30_000);

test.each(["not a URL", "ftp://site.example.test", "file:///tmp/site", "mailto:site@example.test"])(
  "config refuses a non-http(s) public origin: %s", async (origin) => {
    vi.stubEnv("ANGEE_PUBLIC_ORIGIN", origin);
    await expect(resolveConfig({ ...await config(), configFile: false }, "build")).rejects.toThrow("ANGEE_PUBLIC_ORIGIN must be an http(s) URL");
  },
);

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
