import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import workflows from "./index";

describe("workflows addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(workflows)).not.toThrow();
  });
});
