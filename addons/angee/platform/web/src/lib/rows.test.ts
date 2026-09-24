import { describe, expect, test } from "vitest";

import { addonDisplayLabel } from "./rows";

describe("addonDisplayLabel", () => {
  test("preserves the resolved Django label", () => {
    expect(addonDisplayLabel("example", "example.base")).toBe("example");
  });

  test("shows the canonical name when the Django label is unknown", () => {
    expect(addonDisplayLabel("", "example.base")).toBe("example.base");
  });
});
