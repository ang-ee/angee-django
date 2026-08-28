import { expectValidBaseAddon } from "@angee/app/testing";
import { createRouteHref } from "@angee/ui";
import { describe, expect, test } from "vitest";

import portfolio, { INITIATIVE_MODEL, PRODUCT_MODEL } from "./index";
import { PRODUCT_FORM_FIELDS } from "./views/ProductsPage";

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
    expect(portfolio.slots?.map((slot) => slot.id)).toEqual([
      "portfolio.project-fields",
      "portfolio.project-updates",
      "portfolio.initiative-updates",
      "portfolio.task-release",
    ]);
  });

  test("keeps the phasal Product form free of health, dates, and progress", () => {
    const fields = Object.values(PRODUCT_FORM_FIELDS);
    expect(fields).not.toContain("health");
    expect(fields).not.toContain("health_updated_at");
    expect(fields).not.toContain("target_date");
    expect(fields).not.toContain("progress");
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
