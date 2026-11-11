import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import proposals from ".";

describe("proposals addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(proposals)).not.toThrow();
  });
});
