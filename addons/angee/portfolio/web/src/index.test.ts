import { expectValidBaseAddon } from "@angee/app/testing";
import { createRouteHref, Tab } from "@angee/ui";
import * as React from "react";
import { describe, expect, test } from "vitest";

import portfolio, { INITIATIVE_MODEL, PRODUCT_MODEL } from "./index";
import {
  InitiativeUpdatesSection,
  ProjectUpdatesSection,
} from "./update-composer";

describe("portfolio addon manifest", () => {
  test("satisfies rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(portfolio)).not.toThrow();
  });

  test("declares the roadmap and routed product and initiative places", () => {
    expect((portfolio.routes ?? []).map((route) => route.name)).toEqual([
      "portfolio.roadmap",
      "portfolio.products",
      "portfolio.products.record",
      "portfolio.initiatives",
      "portfolio.initiatives.record",
    ]);
    expect(
      portfolio.routes?.find((route) => route.name === "portfolio.roadmap")
        ?.resource,
    ).toBeUndefined();
    expect(
      portfolio.routes?.find((route) => route.name === "portfolio.products")
        ?.resource,
    ).toBe(PRODUCT_MODEL);
    expect(
      portfolio.routes?.find((route) => route.name === "portfolio.initiatives")
        ?.resource,
    ).toBe(INITIATIVE_MODEL);
  });

  test("builds encoded record hrefs", () => {
    const href = createRouteHref(
      (portfolio.routes ?? []).map(({ name, path }) => ({ name, path })),
    );
    expect(href("portfolio.products.record", { id: "product/2.3" })).toBe(
      "/portfolio/products/product%2F2.3",
    );
    expect(href("portfolio.initiatives.record", { id: "north star" })).toBe(
      "/portfolio/initiatives/north%20star",
    );
  });

  test("registers donor fields, update composers, and fixed-in release attribution", () => {
    const children = containerChildren(portfolio.containers);
    expect(children.map(([id]) => id)).toEqual([
      "portfolio.project-fields",
      "portfolio.project-updates",
      "portfolio.initiative-updates",
      "portfolio.task-release",
    ]);

    const updates = children.filter(([id]) => id.endsWith("-updates"));
    expect(updates).toHaveLength(2);
    expect(updates.map(([, { content }]) => {
      if (!React.isValidElement<{ children: React.ReactElement }>(content)) {
        return null;
      }
      return {
        marker: content.type,
        child: content.props.children.type,
      };
    })).toEqual([
      { marker: Tab, child: ProjectUpdatesSection },
      { marker: Tab, child: InitiativeUpdatesSection },
    ]);
  });

  test("registers every portfolio glyph", () => {
    expect(Object.keys(portfolio.icons ?? {}).sort()).toEqual([
      "portfolio",
      "portfolio-initiative",
      "portfolio-product",
      "portfolio-release",
      "portfolio-roadmap",
      "portfolio-update",
    ]);
  });
});

/** The manifest's container children, keyed by id, in declaration order. */
function containerChildren(containers: object | undefined): [string, { content?: unknown }][] {
  return Object.values(containers ?? {}).flatMap((entry) => (Array.isArray(entry) ? entry : [entry]) as Record<string, unknown>[])
    .flatMap((entry) => Object.entries(entry).filter(([id]) => id.includes(".")) as [string, { content?: unknown }][]);
}
