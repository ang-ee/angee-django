import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, test } from "vitest";

import {
  defineAngeePackageVitestConfig,
  defineAngeeWebVitestConfig,
  gqlAliasFor,
} from "../config/vitest";

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

describe("watcher defaults", () => {
  test.each([
    ["package", defineAngeePackageVitestConfig()],
    ["web", defineAngeeWebVitestConfig({ gqlAlias: [] })],
  ])("%s defaults never watch the jj store", (_kind, config) => {
    expect(config.server?.watch?.ignored).toEqual(
      expect.arrayContaining(["**/.jj/**"]),
    );
  });

  test("a caller's own ignores are kept alongside the jj store", () => {
    const config = defineAngeePackageVitestConfig({
      server: { watch: { ignored: ["**/tmp/**"] } },
    });

    expect(config.server?.watch?.ignored).toEqual(
      expect.arrayContaining(["**/.jj/**", "**/tmp/**"]),
    );
  });
});
