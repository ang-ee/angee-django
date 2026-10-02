import { expectValidBaseAddon } from "@angee/app/testing";
import type { ChatterViewContext } from "@angee/ui/runtime";
import { describe, expect, test } from "vitest";

import messaging, { defineMessagingAddon } from "./index";

describe("messaging addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(messaging)).not.toThrow();
  });

  test("registers the chatter tabs and message resources", () => {
    expect(messaging.chatter?.map((entry) => entry.id)).toEqual([
      "comments",
      "activity",
      "sources",
    ]);
    expect((messaging.routes ?? []).map((route) => route.name)).toEqual([
      "messaging.publicWebforms",
      "messaging.publicWebform",
      "messaging.inbox",
      "messaging.inbox.record",
      "messaging.threads",
      "messaging.threads.record",
      "messaging.channels",
      "messaging.channels.record",
    ]);
    expect(messaging.menus?.[0]?.children?.map((item) => item.id)).toEqual([
      "messaging.inbox",
      "messaging.threads",
      "messaging.channels",
    ]);
  });

  test("passes the app's submit key into the built-in Comments tab", () => {
    const configured = defineMessagingAddon({ submitKey: "mod-enter" });
    expect(() => expectValidBaseAddon(configured)).not.toThrow();
    expect(configured.chatter?.[0]?.render?.({} as ChatterViewContext))
      .toMatchObject({ props: { submitKey: "mod-enter" } });
    expect(messaging.chatter?.[0]?.render?.({} as ChatterViewContext))
      .toMatchObject({ props: { submitKey: "enter" } });
  });
});
