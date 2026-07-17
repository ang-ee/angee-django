import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import parties from "./index";

describe("parties addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(parties)).not.toThrow();
  });

  test("registers the overview, people, organization, circle, relationship, handle, review, merge, and directory pages", () => {
    expect((parties.routes ?? []).map((route) => route.name)).toEqual([
      "parties.overview",
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
      "parties.merge",
      "parties.directories",
      "parties.directories.record",
    ]);
  });

  test("registers review under the parties menu", () => {
    const menu = (parties.menus ?? []).find((item) => item.id === "parties");
    expect(menu?.children?.[0]?.route).toBe("parties.overview");
    expect(menu?.children?.map((item) => item.route)).toContain("parties.review");
  });
});
