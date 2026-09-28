import { describe, expect, test } from "vitest";

import { estimateLabel } from "./estimates";
import type { WorkT } from "./i18n";

const t = ((key: string, vars?: Record<string, unknown>) => {
  if (key === "estimate.points") return `${String(vars?.count)} points`;
  if (["estimate.hours", "estimate.days", "estimate.weeks"].includes(key)) {
    return `${String(vars?.count)} ${key.replace("estimate.", "")}`;
  }
  if (key === "estimate.size.unknown") return `Size ${String(vars?.value)}`;
  return key.replace("estimate.size.", "").toUpperCase();
}) as WorkT;

describe("estimateLabel", () => {
  test("hides missing estimates and queues with no estimation scale", () => {
    expect(estimateLabel(null, "LINEAR", t)).toBeNull();
    expect(estimateLabel(3, "NONE", t)).toBeNull();
  });

  test("renders numeric scales in their declared units without rounding", () => {
    expect(estimateLabel(5, "FIBONACCI", t)).toBe("5 points");
    expect(estimateLabel(1.5, "HOURS", t)).toBe("1.5 hours");
    expect(estimateLabel(0.25, "DAYS", t)).toBe("0.25 days");
    expect(estimateLabel(2.5, "WEEKS", t)).toBe("2.5 weeks");
  });

  test("maps canonical T-shirt values and keeps an explicit fallback", () => {
    expect(estimateLabel(8, "TSHIRT", t)).toBe("XL");
    expect(estimateLabel(7, "TSHIRT", t)).toBe("Size 7");
  });
});
