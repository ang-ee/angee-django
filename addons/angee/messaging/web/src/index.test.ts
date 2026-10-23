import { containerChildren, expectValidBaseAddon } from "@angee/app/testing";
import { createRouteHref } from "@angee/ui";
import type { ChatterTabContent, ChatterViewContext } from "@angee/ui/runtime";
import { isValidElement } from "react";
import { describe, expect, test } from "vitest";

import messaging from "./index";
import { RecordChatterPane, type RecordChatterPaneProps } from "./RecordChatterPane";
import { RecordConversationPane, type RecordConversationPaneProps } from "./RecordConversationPane";

describe("messaging addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(messaging)).not.toThrow();
  });

  test("contributes the comment and conversation kinds to the Timeline, the Discussion tab, the Follow control and the Conversations group", () => {
    expect(Object.keys(messaging.containers?.["timeline#kinds"] ?? {})).toEqual(["messaging.comment", "messaging.conversation"]);
    expect(messaging.containers?.["timeline#kinds"]).toMatchObject({
      "messaging.comment": { content: { target: { tab: "messaging.discussion", focus: "message" } } },
      "messaging.conversation": { content: { verb: "Open conversation", target: { tab: "messaging.conversation" } } },
    });
    expect(messaging.containers?.["record#aside"]).toMatchObject({ "messaging.discussion": { sequence: 20 } });
    expect(messaging.containers?.["record#aside"]).toMatchObject({ "messaging.conversation": { content: {
      strip: false, target: { model: "messaging.ThreadAttachment" },
    } } });
    expect(Object.keys(messaging.containers?.["form#chrome"] ?? {})).toEqual(["messaging.follow"]);
    expect(messaging.containers?.["related#groups"]).toMatchObject({ "messaging.conversations": { sequence: 40, content: { label: "Conversations", edge: { model: "messaging.ThreadAttachment" } } } });
  });

  test("passes the shell selection to Discussion and keys its filters by target", () => {
    const context: ChatterViewContext = {
      pathname: "/notes/nte_1", params: { id: "nte_1" },
      view: { kind: "record", type: "notes.Note", sqid: "nte_1" },
    };
    const selection = { target: "msg_1", focus: "message" };
    const discussion = containerChildren(messaging, "record#aside").find(([id]) => id === "messaging.discussion")?.[1].content as ChatterTabContent;
    const rendered = discussion.render?.(context, selection);
    expect(isValidElement<RecordChatterPaneProps>(rendered)).toBe(true);
    if (!isValidElement<RecordChatterPaneProps>(rendered)) throw new Error("Discussion must render its pane");
    expect(rendered.type).toBe(RecordChatterPane);
    expect(rendered.key).toBe(selection.target);
    expect(rendered.props.context).toBe(context);
    expect(rendered.props.selection).toBe(selection);
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

  test("opens an attachment in the conversation drill-in", () => {
    const context: ChatterViewContext = {
      pathname: "/notes/nte_1", params: { id: "nte_1" },
      view: { kind: "record", type: "notes.Note", sqid: "nte_1" },
    };
    const conversation = containerChildren(messaging, "record#aside").find(([id]) => id === "messaging.conversation")?.[1].content as ChatterTabContent;
    const rendered = conversation.render?.(context, { target: "tat_1" });
    if (!isValidElement<RecordConversationPaneProps>(rendered)) throw new Error("Conversation must render its pane");
    expect(rendered.type).toBe(RecordConversationPane);
    expect(rendered.props).toEqual({ context, attachmentId: "tat_1" });
  });

  test("builds message list and record links from the renamed route claims", () => {
    const routeHref = createRouteHref(messaging.routes ?? []);
    expect(routeHref("messaging.messages")).toBe("/messaging/messages");
    expect(routeHref("messaging.messages.record", { id: "message 1" })).toBe("/messaging/messages/message%201");
    expect(messaging.routes?.find((route) => route.resource === "messaging.Message")?.name).toBe("messaging.messages");
  });
});
