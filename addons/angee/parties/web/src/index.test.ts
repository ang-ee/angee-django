import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import parties from "./index";

describe("parties addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(parties)).not.toThrow();
  });

  test("registers the people, organization, circle, relationship, handle, review, and directory pages", () => {
    expect((parties.routes ?? []).map((route) => route.name)).toEqual([
      "parties.people",
      "parties.people.record",
      "parties.organizations",
      "parties.organizations.record",
      "parties.circles",
      "parties.circles.record",
      "parties.relationships",
      "parties.relationships.record",
      "parties.handles",
      "parties.handles.record",
      "parties.review",
      "parties.directories",
      "parties.directories.record",
    ]);
  });

  test("registers review under the parties menu", () => {
    const menu = (parties.menus ?? []).find((item) => item.id === "parties");
    expect(menu?.children?.map((item) => item.route)).toContain("parties.review");
  });
});
