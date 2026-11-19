import { execFile } from "node:child_process";
import { mkdtemp, mkdir, readFile, realpath, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { generateWebRuntime, type WebCodegenInputs } from "@angee/app/web-codegen";

// Regression coverage for the frontend runtime codegen CLI this package ships.
// The suites run the real binary against synthetic manifests in a temp dir —
// no composed host is involved (moved from angee-django's pytest suite when
// the example host dissolved; @angee/app owns the CLI, so the tests live here).
const CODEGEN = fileURLToPath(new URL("../bin/angee-web-codegen.mjs", import.meta.url));
const run = promisify(execFile);

describe("angee-web-codegen", () => {
  let root: string;

  beforeEach(async () => {
    root = await mkdtemp(join(tmpdir(), "angee-web-codegen-"));
  });

  afterEach(async () => {
    await rm(root, { recursive: true, force: true });
  });

  describe("static site assets", () => {
    let runtime: string;
    let web: string;
    let addon: string;
    const assets = { "demo.preview": "./art/preview.png", "demo.icon": "./art/icon.svg", "demo.touch": "./art/touch.png" };
    async function siteManifest(shell: object = {}, declarations: Record<string, string> = assets, installed = false) {
      await writeFile(join(runtime, "web", "manifest.json"), JSON.stringify({
        schema: 1,
        addonPackages: [{ package: "@demo/site", sourceRoot: "src/headless", ...(installed ? {} : { root: "../../linked-addon" }), assets: declarations }],
        deployment: { shell },
      }));
    }
    beforeEach(async () => {
      root = await realpath(root);
      runtime = join(root, "runtime");
      web = join(root, "web");
      addon = join(root, "addon");
      await mkdir(join(runtime, "web"), { recursive: true });
      await mkdir(join(web, "node_modules", "@demo"), { recursive: true });
      await mkdir(join(addon, "src", "headless"), { recursive: true });
      await mkdir(join(addon, "art"));
      await writeFile(join(addon, "package.json"), JSON.stringify({ name: "@demo/site" }));
      await writeFile(join(addon, "src", "headless", "index.ts"), "export default {};");
      for (const file of Object.values(assets)) await writeFile(join(addon, file), "asset bytes");
      await symlink(addon, join(root, "linked-addon"));
      await symlink(addon, join(web, "node_modules", "@demo", "site"));
    });

    it.each([false, true])("resolves package-root assets (installed fallback: %s) without leaking paths into app.ts", async (installed) => {
      await siteManifest({ brand: { name: "Suite", mark: "suite" }, site: {
        description: "Suite tools", icon: "demo.icon", touchIcon: "demo.touch", image: "demo.preview",
      } }, assets, installed);
      const result = await generateWebRuntime({ webRoot: web, runtimeDir: runtime });
      expect(result.site).toEqual({
        title: "Suite", siteName: "Suite", description: "Suite tools",
        icon: join(addon, "art/icon.svg"), touchIcon: join(addon, "art/touch.png"), image: join(addon, "art/preview.png"),
      });
      const app = await readFile(join(runtime, "web/app.ts"), "utf8");
      for (const file of Object.values(assets)) {
        expect(result.inputs.files).toContain(join(addon, file));
        expect(app).not.toContain(join(addon, file));
      }
    });

    it("leaves the site absent without a shell brand or site, and defaults a brand-only title", async () => {
      await siteManifest();
      expect(await generateWebRuntime({ webRoot: web, runtimeDir: runtime })).not.toHaveProperty("site");
      await siteManifest({ brand: { name: "Suite", mark: "suite" } });
      expect((await generateWebRuntime({ webRoot: web, runtimeDir: runtime })).site).toEqual({ title: "Suite", siteName: "Suite" });
      await siteManifest({ brand: { name: "Suite", mark: "suite" }, site: { title: "Chosen" } });
      expect((await generateWebRuntime({ webRoot: web, runtimeDir: runtime })).site?.title).toBe("Chosen");
    });

    it("lists unknown asset choices in sorted order", async () => {
      await siteManifest({ site: { title: "Site", icon: "unknown" } });
      await expect(generateWebRuntime({ webRoot: web, runtimeDir: runtime }))
        .rejects.toThrow('unknown asset "unknown"; declared assets: demo.icon, demo.preview, demo.touch');
    });

    it.each(["missing", "directory", "escaping symlink"])("refuses a %s and registers the input before failure", async (kind) => {
      const file = join(addon, "art/icon.svg");
      await rm(file);
      if (kind === "directory") await mkdir(file);
      if (kind === "escaping symlink") {
        await writeFile(join(root, "outside.svg"), "outside");
        await symlink(join(root, "outside.svg"), file);
      }
      await siteManifest({ site: { title: "Site", icon: "demo.icon" } });
      let inputs: WebCodegenInputs | undefined;
      await expect(generateWebRuntime({ webRoot: web, runtimeDir: runtime, onInputs: (next) => { inputs = next; } }))
        .rejects.toThrow(kind === "escaping symlink" ? "outside package @demo/site" : "must be a regular file");
      expect(inputs?.files).toContain(kind === "escaping symlink" ? join(root, "outside.svg") : file);
    });

    it.each([
      ["icon", "demo.preview"], ["touchIcon", "demo.touch"], ["image", "demo.preview"],
    ])("resolves %s with a PNG", async (slot, name) => {
      await siteManifest({ site: { title: "Site", [slot]: name } });
      expect((await generateWebRuntime({ webRoot: web, runtimeDir: runtime })).site).toHaveProperty(slot);
    });

    it.each([
      ["icon", ".jpg"], ["touchIcon", ".svg"], ["touchIcon", ".ico"], ["image", ".svg"], ["image", ".webp"],
    ])("refuses %s with extension %s", async (slot, extension) => {
      await writeFile(join(addon, `art/wrong${extension}`), "wrong type");
      await siteManifest({ site: { title: "Site", [slot]: "demo.wrong" } }, { "demo.wrong": `./art/wrong${extension}` });
      await expect(generateWebRuntime({ webRoot: web, runtimeDir: runtime })).rejects.toThrow(`site.${slot} asset "demo.wrong" must use`);
    });
  });

  it.each([
    { apps: { desk: { rail: ["notes"], railExpanded: "false" } } },
    { apps: { desk: { rail: [] } } },
    { brand: { name: "Suite" } },
    { hosts: { "BAD.example.test": "notes" } },
  ])("rejects malformed shell configuration before emitting output: %j", async (shell) => {
    const runtime = join(root, "runtime");
    const web = join(root, "web");
    await mkdir(join(runtime, "web"), { recursive: true });
    await writeFile(join(runtime, "web", "manifest.json"), JSON.stringify({
      schema: 1, addonPackages: [], codegen: [], documentRoots: [], deployment: { shell },
    }));
    await expect(run("node", [CODEGEN, "--runtime", runtime, "--web-root", web])).rejects.toThrow("ANGEE_UI.shell");
  });

  it("emits extensioned addon entry imports", async () => {
    // Generated app imports point at concrete TypeScript entry files.
    const runtime = join(root, "runtime");
    const web = join(root, "web");
    const manifestDir = join(runtime, "web");
    await mkdir(manifestDir, { recursive: true });
    await mkdir(web, { recursive: true });
    for (const [pkg, extension] of [
      ["@demo/addon", ".tsx"],
      ["@demo/tools", ".ts"],
    ] as const) {
      const entryDir = join(web, "node_modules", pkg, "src");
      await mkdir(entryDir, { recursive: true });
      await writeFile(
        join(entryDir, "..", "package.json"),
        JSON.stringify({ name: pkg, exports: {} }),
      );
      await writeFile(join(entryDir, `index${extension}`), "export default {};\n");
    }
    await writeFile(
      join(manifestDir, "manifest.json"),
      JSON.stringify({
        schema: 1,
        addonPackages: [
          { package: "@demo/addon", sourceRoot: "src" },
          { package: "@demo/tools", sourceRoot: "src" },
        ],
        codegen: [],
        documentRoots: [],
      }),
    );

    await run("node", [CODEGEN, "--runtime", runtime, "--web-root", web]);

    const appModule = await readFile(join(manifestDir, "app.ts"), "utf8");
    expect(appModule).toContain(
      'import addon0 from "../../web/node_modules/@demo/addon/src/index.tsx";',
    );
    expect(appModule).toContain(
      'import addon1 from "../../web/node_modules/@demo/tools/src/index.ts";',
    );
  });

  it("attaches addon ancestry and appends the deployment layer", async () => {
    const runtime = join(root, "runtime");
    const web = join(root, "web");
    const manifestDir = join(runtime, "web");
    await mkdir(manifestDir, { recursive: true });
    for (const pkg of ["@demo/base", "@demo/product"]) {
      const entryDir = join(web, "node_modules", pkg, "src");
      await mkdir(entryDir, { recursive: true });
      await writeFile(join(entryDir, "..", "package.json"), JSON.stringify({ name: pkg, exports: {} }));
      await writeFile(join(entryDir, "index.ts"), "export default {};\n");
    }
    await writeFile(
      join(manifestDir, "manifest.json"),
      JSON.stringify({
        schema: 1,
        addonPackages: [
          { package: "@demo/base", sourceRoot: "src", app: "demo.base", dependsOn: [] },
          { package: "@demo/product", sourceRoot: "src", app: "demo.product", dependsOn: ["demo.base"] },
        ],
        codegen: [],
        documentRoots: [],
        deployment: { shell: { hosts: { "demo.localhost": "demo" } } },
      }),
    );

    await run("node", [CODEGEN, "--runtime", runtime, "--web-root", web]);

    const appModule = await readFile(join(manifestDir, "app.ts"), "utf8");
    expect(appModule).toContain(
      'export const composedAddons = [addon0, { ...addon1, dependsOn: [addon0.id] }, '
      + '{ id: "deployment", ...{"shell":{"hosts":{"demo.localhost":"demo"}}}, dependsOn: [addon0.id, addon1.id] }] as const;',
    );
    // Theme contributions are read from the addon packages, never the deployment layer.
    expect(appModule).toContain("const contributedThemeDefinitions = [addon0, addon1].flatMap((addon) =>");
  });

  it("emits URL imports and a parallel schema metadata loader", async () => {
    const runtime = join(root, "runtime");
    const web = join(root, "web");
    const schemaDir = join(runtime, "schemas");
    await mkdir(join(runtime, "web"), { recursive: true });
    await mkdir(schemaDir, { recursive: true });
    await mkdir(web, { recursive: true });
    await writeFile(
      join(runtime, "web", "manifest.json"),
      JSON.stringify({ schema: 1, addonPackages: [], codegen: [], documentRoots: [] }),
    );
    for (const name of ["console", "public"]) {
      await writeFile(join(schemaDir, `${name}.graphql`), "type Query { ping: String! }\n");
      await writeFile(join(schemaDir, `${name}.metadata.json`), "{}\n");
    }

    await run("node", [CODEGEN, "--runtime", runtime, "--web-root", web]);

    const appModule = await readFile(join(runtime, "web", "app.ts"), "utf8");
    expect(appModule).toContain('import schema0MetadataUrl from "../schemas/console.metadata.json?url&no-inline";');
    expect(appModule).toContain('import schema1MetadataUrl from "../schemas/public.metadata.json?url&no-inline";');
    expect(appModule).toContain("export async function loadComposedSchemas()");
    expect(appModule).toContain("Promise<unknown>");
    expect(appModule).not.toContain("as AngeeSchemaMetadata");
    expect(appModule).toMatch(/const \[schema0Metadata, schema1Metadata\] = await Promise\.all\(\[\s*fetchSchemaMetadata\(schema0MetadataUrl\),\s*fetchSchemaMetadata\(schema1MetadataUrl\),/);
    expect(appModule).toContain("if (!response.ok) throw new Error");
    expect(appModule).toContain("metadata: schema0Metadata");
    expect(appModule).toContain("metadata: schema1Metadata");
    expect(appModule).not.toContain("export const schemas =");
  }, 30_000);

  it("resolves addon entry and documents from the manifest root", async () => {
    // A composed workspace addon need not be a direct host dependency.
    const runtime = join(root, "runtime");
    const web = join(root, "web");
    const addon = join(root, "addon-web");
    const manifestDir = join(runtime, "web");
    await mkdir(manifestDir, { recursive: true });
    await mkdir(web, { recursive: true });
    await mkdir(join(addon, "src"), { recursive: true });
    await writeFile(
      join(addon, "package.json"),
      JSON.stringify({ name: "@demo/addon", exports: {} }),
    );
    await writeFile(join(addon, "src", "index.tsx"), "export default {};\n");
    await writeFile(
      join(addon, "src", "documents.demo.ts"),
      "export const Demo = /* GraphQL */ `query Demo { ping }`;\n",
    );

    const schemaDir = join(web, "node_modules", "@demo", "schema", "schema");
    await mkdir(schemaDir, { recursive: true });
    await writeFile(join(schemaDir, "demo.graphql"), "type Query { ping: String! }\n");

    await writeFile(
      join(manifestDir, "manifest.json"),
      JSON.stringify({
        schema: 1,
        addonPackages: [
          { package: "@demo/addon", root: "../../addon-web", sourceRoot: "src" },
        ],
        codegen: [
          {
            schema: "demo",
            package: "@demo/schema",
            sdl: "schema/demo.graphql",
            documents: "documents.demo.ts",
            types: false,
          },
        ],
        documentRoots: [
          {
            kind: "package",
            package: "@demo/addon",
            path: "node_modules/@demo/addon/src",
          },
        ],
      }),
    );

    await run("node", [CODEGEN, "--runtime", runtime, "--web-root", web]);

    const appModule = await readFile(join(manifestDir, "app.ts"), "utf8");
    const generatedDocuments = await readFile(
      join(runtime, "gql", "demo", "graphql.ts"),
      "utf8",
    );
    expect(appModule).toContain('import addon0 from "../../addon-web/src/index.tsx";');
    expect(generatedDocuments).toContain("DemoDocument");

    // A stack's live export in runtime/schemas/external wins over the committed SDL.
    const liveDir = join(runtime, "schemas", "external");
    await mkdir(liveDir, { recursive: true });
    await writeFile(join(liveDir, "demo.graphql"), "type Query { ping: String! pong: String! }\n");
    await writeFile(
      join(addon, "src", "documents.demo.ts"),
      "export const Demo = /* GraphQL */ `query Demo { ping }`;\nexport const Pong = /* GraphQL */ `query Pong { pong }`;\n",
    );
    await run("node", [CODEGEN, "--runtime", runtime, "--web-root", web]);
    expect(await readFile(join(runtime, "gql", "demo", "graphql.ts"), "utf8")).toContain("PongDocument");
  }, 30_000);
});
