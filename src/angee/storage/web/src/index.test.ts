import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import storage from "./index";

describe("storage addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(storage)).not.toThrow();
  });
});
