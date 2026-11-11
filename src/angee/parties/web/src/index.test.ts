import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import parties from "./index";

describe("parties addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(parties)).not.toThrow();
  });
});
