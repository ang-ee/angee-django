import { expectValidBaseAddon } from "@angee/app/testing";
import { createRouteHref } from "@angee/ui";
import type { ChatterTabContent, ChatterViewContext, ContainerChild } from "@angee/ui/runtime";
import { describe, expect, test } from "vitest";

import messaging, { defineMessagingAddon, recordCommentsTab } from "./index";

describe("messaging addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(messaging)).not.toThrow();
  });

  test("replaces the framework's placeholder tabs with its own, keeping their old ids as aliases", () => {
    const aside = messaging.containers?.["record#aside"] as Record<string, unknown>;
    expect(aside).toMatchObject({
      "chatter.comments": { remove: true },
      "chatter.activity": { remove: true },
      "messaging.comments": { sequence: 10, content: { aliases: ["comments"] } },
      "messaging.activity": { sequence: 20, content: { aliases: ["activity"] } },
      "messaging.sources": { sequence: 30, content: { aliases: ["sources"] } },
    });
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

  test("passes the app's submit key into the built-in Comments tab", () => {
    const comments = (addon: typeof messaging) =>
      (addon.containers?.["record#aside"] as Record<string, ContainerChild<ChatterTabContent>>)["messaging.comments"]!;
    const configured = defineMessagingAddon({ submitKey: "mod-enter" });
    expect(() => expectValidBaseAddon(configured)).not.toThrow();
    expect(comments(configured).content.render?.({} as ChatterViewContext))
      .toMatchObject({ props: { submitKey: "mod-enter" } });
    expect(comments(messaging).content.render?.({} as ChatterViewContext))
      .toMatchObject({ props: { submitKey: "enter" } });
  });

  test("recordCommentsTab builds the same Comments tab for another addon's aside", () => {
    const tab = recordCommentsTab({ submitKey: "mod-enter", aliases: ["comments"] });
    expect(tab).toMatchObject({ sequence: 10, content: { label: "Comments", icon: "comments", aliases: ["comments"] } });
    expect(tab.content.render?.({} as ChatterViewContext)).toMatchObject({ props: { submitKey: "mod-enter" } });
    expect(recordCommentsTab().content.aliases).toBeUndefined();
  });
});
