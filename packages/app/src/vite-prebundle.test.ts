import { mkdirSync, mkdtempSync, rmSync, utimesSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, test } from "vitest";

import { angeePrebundleForce } from "../config/vite";

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
});
