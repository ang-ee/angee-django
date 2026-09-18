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

  test("matches an override written in the backend's casing against a GraphQL enum read", () => {
    // The row carries the enum member name; the author wrote the backend token.
    // Without the normalized second pass the override is silently inert and the
    // value falls through to the shared vocabulary -- which reads `open` as
    // success, colouring an open task the same green as a done one.
    const taskTones = { open: "info", done: "success", dropped: "neutral" } as const;

    expect(statusTone("OPEN", taskTones)).toBe("info");
    expect(statusTone("DONE", taskTones)).toBe("success");
    expect(statusTone("DROPPED", taskTones)).toBe("neutral");
    // The exact spelling still works, and still wins.
    expect(statusTone("open", taskTones)).toBe("info");
  });

  test("keeps an exact override ahead of a differently-cased sibling", () => {
    expect(statusTone("OPEN", { OPEN: "danger", open: "info" })).toBe("danger");
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
