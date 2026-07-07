import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import tags from "./index";

describe("angee.tags addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(tags)).not.toThrow();
  });

  test("names the tag resource on its list route so relations can follow it", () => {
    const resources = (tags.routes ?? [])
      .map((route) => route.resource)
      .filter((resource): resource is string => Boolean(resource));
    expect(resources).toEqual(["tags.Tag"]);
  });

  test("contributes the record-scoped Tags chatter tab", () => {
    expect((tags.chatter ?? []).map((tab) => tab.id)).toContain("tags");
  });
});
