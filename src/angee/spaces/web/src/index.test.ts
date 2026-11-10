import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import spaces from "./index";

describe("spaces addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(spaces)).not.toThrow();
  });
});
