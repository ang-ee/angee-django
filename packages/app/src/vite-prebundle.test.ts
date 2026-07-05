import { mkdirSync, mkdtempSync, rmSync, utimesSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, test } from "vitest";
import type { ConfigEnv, Plugin, UserConfig } from "vite";

import { angeePrebundleForce, angeePrebundleForcePlugin } from "../config/vite";

// The `config` hook ignores its plugin-context `this`, so drop it for the call.
type ConfigHookFn = (config: UserConfig, env: ConfigEnv) => unknown;

/** Invoke a plugin's `config` hook (function or object form) for one command. */
function runConfigHook(plugin: Plugin, command: "serve" | "build"): unknown {
  const hook = plugin.config;
  const handler = (typeof hook === "function" ? hook : hook?.handler) as ConfigHookFn | undefined;
  const env: ConfigEnv = { command, mode: command === "serve" ? "development" : "production" };
  return handler?.({}, env);
}

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
    expect(runConfigHook(plugin, "serve")).toEqual({ optimizeDeps: { force: true } });
    // Unchanged tree → cached.
    expect(runConfigHook(plugin, "serve")).toEqual({ optimizeDeps: { force: false } });

    // Edit the source; a build in between must NOT swallow it — the next serve
    // still forces the re-optimize.
    const later = Date.now() / 1000 + 10;
    utimesSync(source, later, later);
    expect(runConfigHook(plugin, "build")).toBeUndefined();
    expect(runConfigHook(plugin, "serve")).toEqual({ optimizeDeps: { force: true } });
  });
});
