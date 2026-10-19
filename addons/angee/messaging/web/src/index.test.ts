import { expectValidBaseAddon } from "@angee/app/testing";
import { createRouteHref } from "@angee/ui";
import { describe, expect, test } from "vitest";

import messaging from "./index";

describe("messaging addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(messaging)).not.toThrow();
  });

  test("contributes the comment and conversation kinds to the Timeline and the Discussion tab", () => {
    expect(Object.keys(messaging.containers?.["timeline#kinds"] ?? {})).toEqual(["messaging.comment", "messaging.conversation"]);
    expect(messaging.containers?.["record#aside"]).toMatchObject({ "messaging.discussion": { sequence: 20 } });
  });

  test("registers the message resources", () => {
    expect((messaging.routes ?? []).map((route) => route.name)).toEqual([
      "messaging.publicWebforms",
      "messaging.publicWebform",
      "messaging.messages",
      "messaging.messages.record",
      "messaging.threads",
      "messaging.threads.record",
      "messaging.channels",
      "messaging.channels.record",
    ]);
    expect(messaging.menus["messaging.channels"]).toMatchObject({ parent: "messaging", group: "platform" });
    expect(messaging.menus["messaging.messages"]).toMatchObject({ label: "Messages", route: "messaging.messages", sequence: 10 });
    expect(messaging.menus["messaging.threads"]?.sequence).toBe(20);
  });

  test("builds message list and record links from the renamed route claims", () => {
    const routeHref = createRouteHref(messaging.routes ?? []);
    expect(routeHref("messaging.messages")).toBe("/messaging/messages");
    expect(routeHref("messaging.messages.record", { id: "message 1" })).toBe("/messaging/messages/message%201");
    expect(messaging.routes?.find((route) => route.resource === "messaging.Message")?.name).toBe("messaging.messages");
  });
});
