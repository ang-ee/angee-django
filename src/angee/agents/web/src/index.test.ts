import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import agents from "./index";

describe("agents addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(agents)).not.toThrow();
  });
});
