import { afterEach, describe, expect, test } from "vitest";

import { formatNumber } from "./format-number";
import { setHumanDateLocale } from "./human-locale";

describe("formatNumber", () => {
  afterEach(() => setHumanDateLocale("en"));

  test("uses native grouped and compact formats", () => {
    expect(formatNumber(1234.5, { locale: "en-US" })).toBe("1,234.5");
    expect(formatNumber(1200, { locale: "en-US", notation: "compact" })).toBe("1.2K");
  });

  test("passes numeric strings directly to Intl without losing precision", () => {
    const value = "12345678901234567890.123456789";
    expect(formatNumber(12345678901234567890n, { locale: "en-US" }))
      .toBe("12,345,678,901,234,567,890");
    expect(formatNumber(value, {
      locale: "en-US",
      maximumFractionDigits: 9,
    })).toBe("12,345,678,901,234,567,890.123456789");
  });

  test("uses the app language by default and lets an explicit locale win", () => {
    setHumanDateLocale("fr-FR");
    expect(formatNumber(1234.5)).toBe(new Intl.NumberFormat("fr-FR").format(1234.5));
    expect(formatNumber(1234.5, { locale: "en-US" })).toBe("1,234.5");
  });

  test("supports ungrouped widget values", () => {
    expect(formatNumber(-1234.5, {
      locale: "en-US",
      useGrouping: false,
      maximumFractionDigits: 20,
    })).toBe("-1234.5");
  });

  test("uses each currency's native minor-unit digits", () => {
    expect(formatNumber(1234.5, {
      locale: "en-US",
      style: "currency",
      currency: "JPY",
    })).toMatch(/1,235$/);
    expect(formatNumber(1234.5, {
      locale: "en-US",
      style: "currency",
      currency: "BHD",
    })).toMatch(/1,234\.500$/);
  });

  test.each([null, undefined, "", " "])("renders %j as empty", (value) => {
    expect(formatNumber(value)).toBe("");
  });

  test.each([NaN, Infinity, -Infinity, "Infinity", "NaN", "not a number"])("renders non-finite or invalid %s as empty", (value) => {
    expect(formatNumber(value)).toBe("");
  });
});
