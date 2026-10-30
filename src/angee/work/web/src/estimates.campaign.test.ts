import { describe, expect, test, vi } from "vitest";

import { estimateLabel } from "./estimates";
import type { WorkT } from "./i18n";

describe("estimate scale translation contract", () => {
  test.each([
    ["LINEAR", "estimate.points"], ["FIBONACCI", "estimate.points"], ["EXPONENTIAL", "estimate.points"],
    ["HOURS", "estimate.hours"], ["DAYS", "estimate.days"], ["WEEKS", "estimate.weeks"],
  ])("%s keeps zero, singular and fractional values", (scale, key) => {
    const translate = vi.fn<WorkT>((key, vars) => `${key}:${String(vars?.count)}`);
    for (const value of [0, 1, 0.25, 1.5, 2]) {
      expect(estimateLabel(value, scale, translate)).toBe(`${key}:${value}`);
      expect(translate).toHaveBeenLastCalledWith(key, { count: value });
      expect(estimateLabel(value, ` ${scale.toLowerCase()} `, translate)).toBe(`${key}:${value}`);
    }
  });

  test.each([[1, "xs"], [2, "s"], [3, "m"], [5, "l"], [8, "xl"], [13, "xxl"]] as const)(
    "T-shirt estimate %s maps to %s", (value, size) => {
      expect(estimateLabel(value, "TSHIRT", (key) => key)).toBe(`estimate.size.${size}`);
    },
  );

  test.each([null, undefined, "1.5", Number.NaN, Infinity, -Infinity])("invalid estimate %s has no label", (value) => {
    const translate = vi.fn<WorkT>();
    expect(estimateLabel(value, "HOURS", translate)).toBeNull();
    expect(translate).not.toHaveBeenCalled();
  });

  test("NONE renders no default and unknown T-shirt values keep their exact value", () => {
    const translate = vi.fn<WorkT>((key) => key);
    expect(estimateLabel(2, "none", translate)).toBeNull();
    expect(translate).not.toHaveBeenCalled();
    expect(estimateLabel(0.5, "TSHIRT", translate)).toBe("estimate.size.unknown");
    expect(translate).toHaveBeenCalledExactlyOnceWith("estimate.size.unknown", { value: 0.5 });
  });
});
