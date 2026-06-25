import { describe, expect, test } from "vitest";

import { statusTone } from "./status-tones";

describe("statusTone", () => {
  test("resolves shared cross-domain status vocabulary", () => {
    expect(statusTone("connecting")).toBe("warning");
    expect(statusTone("CLOSED")).toBe("warning");
    expect(statusTone("ready")).toBe("success");
  });

  test("keeps the default unknown and empty behavior", () => {
    expect(statusTone("bespoke")).toBe("brand");
    expect(statusTone("")).toBe("neutral");
    expect(statusTone(null)).toBe("neutral");
  });

  test("lets daemon-style surfaces choose a quiet unknown fallback", () => {
    expect(statusTone("bespoke", undefined, { unknownTone: "neutral" })).toBe(
      "neutral",
    );
  });

  test("keeps explicit overrides authoritative", () => {
    expect(
      statusTone("connecting", { connecting: "info" }, { unknownTone: "neutral" }),
    ).toBe("info");
  });
});
