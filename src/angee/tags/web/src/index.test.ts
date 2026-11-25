import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import tags from "./index";

describe("angee.tags addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(tags)).not.toThrow();
  });

  test("registers the record-tags widget other forms resolve by key and the Tag form by model label", () => {
    expect(tags.widgets?.["angee.tags.recordTags"]).toBeDefined();
    expect(tags.forms?.["tags.Tag"]).toBeDefined();
  });

  test("guard: the record-tags widget it registers writes its own value, so a record form leaves it its own editability", () => {
    expect(tags.widgets?.["angee.tags.recordTags"]).toHaveProperty("writesOwnValue", true);
  });

  test("routes the tag vocabulary and its categories under the Tags menu", () => {
    expect(tags.routes?.map((route) => route.name)).toEqual(
      expect.arrayContaining(["tags.tags", "tags.categories"]),
    );
    expect(tags.menus?.["tags.categories"]).toMatchObject({ route: "tags.categories", parent: "tags" });
  });
});
