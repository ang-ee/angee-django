import { describe, expect, test } from "vitest";

import { estimateLabel } from "./estimates";
import type { WorkT } from "./i18n";

const t = ((key: string, vars?: Record<string, unknown>) => {
  if (key === "estimate.points") return `${String(vars?.count)} points`;
  if (key === "estimate.size.unknown") return `Size ${String(vars?.value)}`;
  return key.replace("estimate.size.", "").toUpperCase();
}) as WorkT;

describe("estimateLabel", () => {
  test("hides missing estimates and queues with no estimation scale", () => {
    expect(estimateLabel(null, "LINEAR", t)).toBeNull();
    expect(estimateLabel(3, "NONE", t)).toBeNull();
  });

  test("renders numeric scales as points", () => {
    expect(estimateLabel(5, "FIBONACCI", t)).toBe("5 points");
  });

  test("maps canonical T-shirt values and keeps an explicit fallback", () => {
    expect(estimateLabel(8, "TSHIRT", t)).toBe("XL");
    expect(estimateLabel(7, "TSHIRT", t)).toBe("Size 7");
  });
});
