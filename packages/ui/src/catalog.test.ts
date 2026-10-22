import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { expect, test } from "vitest";
import { componentCatalog, parseCatalog } from "./catalog";

// Like the architecture guard's full-tree scans, compiler audits need a bounded
// build budget: this scan measured 7.5s alongside the complete UI suite.
test("the catalog and token names match their owning declarations", { timeout: 30_000 }, () => {
  // Storybook owns CSF and its build-time generator. Exercise that CLI without
  // importing tooling code or its dependencies into the framework package.
  const root = fileURLToPath(new URL("../../../", import.meta.url));
  expect(() => execFileSync(process.execPath, ["packages/storybook/scripts/catalog.mjs", "--check"], {
    cwd: root, encoding: "utf8",
  })).not.toThrow();
});

test("the import boundary rejects obsolete fields and unexplained infrastructure", () => {
  expect(() => parseCatalog([{ ...componentCatalog[0], role: "component" }])).toThrow();
  expect(() => parseCatalog([{
    package: "@angee/ui", name: "MissingReason", source: "packages/ui/src/index.ts",
    kind: null, status: "stable", storyId: null,
  }])).toThrow();
});
