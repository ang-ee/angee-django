import { expectValidBaseAddon } from "@angee/app/testing";
import { createRouteHref } from "@angee/ui";
import type { ChatterTabContent, ChatterViewContext, ContainerChild } from "@angee/ui/runtime";
import { describe, expect, test } from "vitest";

import messaging, { defineMessagingAddon, recordCommentsTab } from "./index";

describe("messaging addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(messaging)).not.toThrow();
  });

  test("contributes gated Comments and Conversations; provenance owns History", () => {
    const aside = messaging.containers?.["record#aside"] as Record<string, unknown>;
    expect(aside).toMatchObject({
      "messaging.comments": { sequence: 10, content: { label: "Comments" } },
      "messaging.related": { sequence: 60, content: { label: "Conversations" } },
    });
  });

  test("offers the Conversations tab only where the record's type can retain source conversations", () => {
    const sources = (messaging.containers?.["record#aside"] as Record<string, ContainerChild<ChatterTabContent>>)["messaging.related"]!;
    const record = (recordEdges?: readonly string[]): ChatterViewContext => ({
      pathname: "/records/rec_1", params: { id: "rec_1" },
      route: { name: "record", path: "/records/$id", viewType: "example/record", modelLabel: "example.Record", ...(recordEdges ? { recordEdges } : {}) },
      view: { kind: "record", type: "example/record", sqid: "rec_1" },
    });
    expect(sources.content.when?.(record(["messaging.ThreadAttachment"]))).toBe(true);
    expect(sources.content.when?.(record(["storage.FileAttachment"]))).toBe(false);
    expect(sources.content.when?.(record())).toBe(false);
  });

  test("offers Comments only where the record's type keeps chatter, not on every thread attachment target", () => {
    const comments = (messaging.containers?.["record#aside"] as Record<string, ContainerChild<ChatterTabContent>>)["messaging.comments"]!;
    const record = (recordEdges: readonly string[]): ChatterViewContext => ({
      pathname: "/records/rec_1", params: { id: "rec_1" },
      route: { name: "record", path: "/records/$id", viewType: "example/record", modelLabel: "example.Record", recordEdges },
      view: { kind: "record", type: "example/record", sqid: "rec_1" },
    });
    expect(comments.content.when?.(record(["messaging.ThreadAttachment", "messaging.ThreadAttachment:chatter"]))).toBe(true);
    // A file retains source conversations but keeps no chatter of its own.
    expect(comments.content.when?.(record(["messaging.ThreadAttachment", "tags.TagAssignment"]))).toBe(false);
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
    const tab = recordCommentsTab({ submitKey: "mod-enter" });
    expect(tab).toMatchObject({ sequence: 10, content: { label: "Comments", icon: "comments" } });
    expect(tab.content.render?.({} as ChatterViewContext)).toMatchObject({ props: { submitKey: "mod-enter" } });
  });
});
