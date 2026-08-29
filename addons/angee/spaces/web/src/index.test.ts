import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import spaces from "./index";

describe("spaces addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(spaces)).not.toThrow();
  });

  test("registers group collection and detail routes", () => {
    expect((spaces.routes ?? []).map((route) => route.name)).toEqual([
      "spaces.groups",
      "spaces.groups.record",
    ]);
  });

  test("registers the Spaces menu at the groups route", () => {
    expect(spaces.menus).toEqual([
      expect.objectContaining({
        id: "spaces",
        label: "Spaces",
        route: "spaces.groups",
      }),
    ]);
  });
});
