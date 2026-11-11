import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import platform from "./index";

describe("platform addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(platform)).not.toThrow();
  });
});
