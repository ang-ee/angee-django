import { describe, expect, test } from "vitest";

import { addonDisplayLabel } from "./rows";

describe("addonDisplayLabel", () => {
  test("preserves the resolved Django label", () => {
    expect(addonDisplayLabel("arp", "arp.base")).toBe("arp");
  });

  test("shows the canonical name when the Django label is unknown", () => {
    expect(addonDisplayLabel("", "arp.base")).toBe("arp.base");
  });
});
