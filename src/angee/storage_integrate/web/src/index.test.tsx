import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import storageIntegrate from "./index";

describe("angee.storage_integrate addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(storageIntegrate)).not.toThrow();
  });
});
