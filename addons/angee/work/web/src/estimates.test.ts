import { createAngeeI18nInstance } from "@angee/ui/runtime/i18n";
import { expect, test } from "vitest";

import { estimateLabel } from "./estimates";
import { enWorkMessages, type WorkT } from "./i18n";

const translator = createAngeeI18nInstance({ work: enWorkMessages });
const t = ((key: string, vars?: Record<string, unknown>) => translator.t(key, { ns: "work", ...vars })) as unknown as WorkT;

test.each([
  ["HOURS", 1, "1 hour"], ["HOURS", 0.5, "0.5 hours"], ["HOURS", 0, "0 hours"],
  ["DAYS", 1, "1 day"], ["DAYS", 1.25, "1.25 days"], ["WEEKS", 1, "1 week"],
  ["WEEKS", 2.5, "2.5 weeks"], ["LINEAR", 1, "1 point"], ["FIBONACCI", 2, "2 points"],
  ["EXPONENTIAL", 4, "4 points"], ["TSHIRT", 3, "M"],
] as const)("the board's estimate column renders %s estimate %s as %s", (scale, value, expected) => {
  expect(estimateLabel(value, scale, t)).toBe(expected);
});

test("the estimate is omitted when the queue disables estimates", () => {
  expect(estimateLabel(3, "NONE", t)).toBeNull();
});
