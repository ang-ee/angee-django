import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
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
