import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import knowledge from "./index";

describe("knowledge addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(knowledge)).not.toThrow();
  });
});
