import { describe, expect, test } from "vitest";

import { parsePortfolioUpdateValues } from "./update-composer";

describe("portfolio update composer", () => {
  test("normalizes the required health assertion and optional narrative", () => {
    expect(
      parsePortfolioUpdateValues({
        health: "at_risk",
        body: "  Dependencies need attention.  ",
      }),
    ).toEqual({
      health: "AT_RISK",
      body: "Dependencies need attention.",
    });
  });

  test("sends an empty body when the narrative is omitted", () => {
    expect(parsePortfolioUpdateValues({ health: "ON_TRACK" })).toEqual({
      health: "ON_TRACK",
      body: "",
    });
  });

  test("rejects a missing health assertion", () => {
    expect(() => parsePortfolioUpdateValues({ body: "No health" })).toThrow(
      /required field "health"/,
    );
  });

  test("rejects values outside the portfolio health enum", () => {
    expect(() => parsePortfolioUpdateValues({ health: "UNKNOWN" })).toThrow(
      /health assertion is required/,
    );
  });
});
