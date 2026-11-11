import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import posts from "./index";

describe("posts addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(posts)).not.toThrow();
  });
});
