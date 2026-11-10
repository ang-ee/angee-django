import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import portfolio from "./index";

describe("portfolio addon manifest", () => {
  test("satisfies rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(portfolio)).not.toThrow();
  });
});
