import { describe, expect, test } from "vitest";

import { parsePortfolioUpdateValues } from "./update-composer";

const healthOptions = [
  { value: "ON_TRACK", label: "On track" },
  { value: "AT_RISK", label: "At risk" },
  { value: "OFF_TRACK", label: "Off track" },
];

describe("portfolio update composer", () => {
  test("normalizes the required health assertion and optional narrative", () => {
    expect(
      parsePortfolioUpdateValues({
        health: "at_risk",
        body: "  Dependencies need attention.  ",
      }, healthOptions),
    ).toEqual({
      health: "AT_RISK",
      body: "Dependencies need attention.",
    });
  });

  test("sends an empty body when the narrative is omitted", () => {
    expect(parsePortfolioUpdateValues({ health: "ON_TRACK" }, healthOptions)).toEqual({
      health: "ON_TRACK",
      body: "",
    });
  });

  test("rejects a missing health assertion", () => {
    expect(() => parsePortfolioUpdateValues({ body: "No health" }, healthOptions)).toThrow(
      /required field "health"/,
    );
  });

  test("rejects values outside the portfolio health enum", () => {
    expect(() => parsePortfolioUpdateValues({ health: "UNKNOWN" }, healthOptions)).toThrow(
      /health assertion is required/,
    );
  });

  test("accepts a newly declared metadata option without a second member list", () => {
    expect(parsePortfolioUpdateValues(
      { health: "DEFERRED" },
      [{ value: "DEFERRED", label: "Deferred" }],
    ).health).toBe("DEFERRED");
  });
});
