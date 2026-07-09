import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import money from "./index";

describe("money addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(money)).not.toThrow();
  });

  test("contributes the money widget renderer", () => {
    expect(money.widgets?.money).toBeDefined();
  });

  test("contributes the currency administration menu", () => {
    expect(money.menus?.[0]).toMatchObject({
      id: "money",
      label: "Money",
      sidebar: true,
      children: [
        { id: "money.currencies", label: "Currencies", route: "money.currencies" },
        { id: "money.rates", label: "Rates", route: "money.rates" },
      ],
    });
  });

  test("routes currencies and rates through their model resources", () => {
    expect(money.routes).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          name: "money.currencies",
          path: "/money/currencies",
          resource: "money.Currency",
        }),
        expect.objectContaining({
          name: "money.rates",
          path: "/money/rates",
          resource: "money.CurrencyRate",
        }),
      ]),
    );
  });
});
