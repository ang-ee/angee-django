import { expectValidBaseAddon } from "@angee/app/testing";
import { createRouteHref } from "@angee/ui";
import { describe, expect, test } from "vitest";

import { enPartiesMessages } from "./i18n";
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
    expect(
      (parties.routes ?? []).find((route) => route.name === "parties.merge")
        ?.parent,
    ).toBe("parties.review");
    expect([
      enPartiesMessages["handle.contact"],
      enPartiesMessages["review.party"],
      enPartiesMessages["relationship.party"],
    ]).toEqual(["Party", "Party", "Party"]);
    expect(
      Object.values(enPartiesMessages).filter((value) => /\bcontacts?\b/i.test(value)),
    ).toEqual([]);
  });

  test("builds list, record, and merge hrefs from its declared route templates", () => {
    const routeHref = createRouteHref(
      (parties.routes ?? []).map(({ name, path }) => ({ name, path })),
    );

    expect(routeHref("parties.people")).toBe("/parties/people");
    expect(routeHref("parties.people.record", { id: "person 1" })).toBe(
      "/parties/people/person%201",
    );
    expect(routeHref("parties.merge", { left: "left/1", right: "right 2" }))
      .toBe("/parties/merge/left%2F1/right%202");
  });
});
