import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import brandkit from "./index";

describe("angee.brandkit addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(brandkit)).not.toThrow();
  });

  test("registers the kit form by model label and the contrast widget by its backend key", () => {
    expect(brandkit.forms?.["brandkit.BrandKit"]).toBeDefined();
    expect(brandkit.widgets?.["angee.brandkit.contrastWarnings"]).toBeDefined();
  });

  test("places the Brand kits page in Settings", () => {
    expect(brandkit.routes?.map((route) => route.name)).toEqual(expect.arrayContaining(["brandkit.kits"]));
    expect(brandkit.menus?.brandkit).toMatchObject({
      route: "brandkit.kits",
      group: "platform",
      requires: "brandkit.BrandKit#create",
    });
  });
});
