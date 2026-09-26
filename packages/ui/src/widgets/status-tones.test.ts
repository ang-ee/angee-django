import { describe, expect, test } from "vitest";

import { statusTone } from "./status-tones";

describe("statusTone", () => {
  test("resolves shared cross-domain status vocabulary", () => {
    expect(statusTone("connecting")).toBe("warning");
    expect(statusTone("CLOSED")).toBe("warning");
    expect(statusTone("ready")).toBe("success");
    expect(statusTone("WON")).toBe("success");
    expect(statusTone("SUCCEEDED")).toBe("success");
    expect(statusTone("STARTED")).toBe("info");
    expect(statusTone("WAITING")).toBe("warning");
    expect(statusTone("SCHEDULED")).toBe("neutral");
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

  test("resolves contributed vocabulary while preserving explicit overrides", () => {
    const options = { statusTones: { reviewed: "accent", queued: "brand" }, unknownTone: "neutral" } as const;
    expect(statusTone(" REVIEWED ", undefined, options)).toBe("accent");
    expect(statusTone("ready", undefined, options)).toBe("success");
    expect(statusTone("queued", undefined, options)).toBe("brand");
    expect(statusTone("REVIEWED", { REVIEWED: "danger" }, options)).toBe("danger");
    expect(statusTone("reviewed")).toBe("brand");
  });

  test("keeps pairing lifecycle states on the shared vocabulary", () => {
    const pairingOverrides = {
      PAIRED: "success",
      LOGGED_OUT: "danger",
    } as const;

    expect(statusTone("PAIRED", pairingOverrides)).toBe("success");
    expect(statusTone("STARTING", pairingOverrides)).toBe("warning");
    expect(statusTone("STOPPED", pairingOverrides)).toBe("neutral");
  });
});
