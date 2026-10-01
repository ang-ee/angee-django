import { mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { loadConfigFromFile } from "vite";
import { describe, expect, test } from "vitest";

import { gqlAliasFor } from "../config/vitest";

describe("gqlAliasFor", () => {
  test("explains how to materialize a missing composed runtime", () => {
    const missingRuntime = join(
      dirname(fileURLToPath(import.meta.url)),
      "__missing_runtime_gql__",
    );

    expect(() => gqlAliasFor(missingRuntime)).toThrow(
      /Compose the stack first, then run pnpm codegen from the stack web host/,
    );
  });
});

test("the emitted addon Vitest config loads the public web defaults", async () => {
  const appRoot = fileURLToPath(new URL("..", import.meta.url));
  const template = readFileSync(
    new URL("../../../templates/addons/web/template/vitest.config.ts.jinja", import.meta.url),
    "utf8",
  );
  // The config is static; require rendering if the template gains substitutions.
  expect(template).not.toMatch(/\{[{%#]/);

  const addonRoot = mkdtempSync(join(tmpdir(), "angee-addon-vitest-"));
  try {
    mkdirSync(join(addonRoot, "node_modules", "@angee"), { recursive: true });
    symlinkSync(appRoot, join(addonRoot, "node_modules", "@angee", "app"), "dir");
    writeFileSync(join(addonRoot, "package.json"), '{"type":"module"}\n');
    const configFile = join(addonRoot, "vitest.config.ts");
    writeFileSync(configFile, template);

    const loaded = await loadConfigFromFile(
      { command: "serve", mode: "test" }, configFile, addonRoot,
    );

    expect(loaded?.config).toMatchObject({
      resolve: { alias: [] },
      test: {
        environment: "node",
        include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
      },
    });
  } finally {
    rmSync(addonRoot, { recursive: true, force: true });
  }
});
