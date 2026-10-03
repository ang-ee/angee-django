import { execFile } from "node:child_process";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

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
        deployment: { shell: { perspective: null } },
      }),
    );

    await run("node", [CODEGEN, "--runtime", runtime, "--web-root", web]);

    const appModule = await readFile(join(manifestDir, "app.ts"), "utf8");
    expect(appModule).toContain(
      'export const composedAddons = [addon0, { ...addon1, dependsOn: [addon0.id] }, '
      + '{ id: "deployment", ...{"shell":{"perspective":null}}, dependsOn: [addon0.id, addon1.id] }] as const;',
    );
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
  }, 30_000);
});
