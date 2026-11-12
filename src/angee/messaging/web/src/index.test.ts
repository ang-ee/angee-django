import { containerChildren, expectValidBaseAddon } from "@angee/app/testing";
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
});
