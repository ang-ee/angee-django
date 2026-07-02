import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import messaging from "./index";

describe("messaging addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(messaging)).not.toThrow();
  });

  test("registers the chatter tabs and message resources", () => {
    expect(messaging.chatter?.map((entry) => entry.id)).toEqual([
      "comments",
      "activity",
    ]);
    expect((messaging.routes ?? []).map((route) => route.name)).toEqual([
      "messaging.inbox",
      "messaging.inbox.record",
      "messaging.threads",
      "messaging.threads.record",
    ]);
  });
});
