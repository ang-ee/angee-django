import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import money from "./index";

describe("money addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(money)).not.toThrow();
  });

  test("contributes the money widget renderer", () => {
    // The addon's whole job: teach the UI the backend-owned `"money"` widget key.
    expect(money.widgets?.money).toBeDefined();
  });
});
