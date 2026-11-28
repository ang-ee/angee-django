import { containerChildren, expectValidBaseAddon } from "@angee/app/testing";
import { statusTone } from "@angee/ui";
import type { ChatterTabContent, ChatterViewContext } from "@angee/ui/runtime";
import { isValidElement } from "react";
import { describe, expect, test } from "vitest";

import messaging, { MESSAGE_STATUS_TONES, NewMessageAction } from "./index";
import { RecordChatterPane, type RecordChatterPaneProps } from "./RecordChatterPane";
import { RecordConversationPane, type RecordConversationPaneProps } from "./RecordConversationPane";

describe("messaging addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(messaging)).not.toThrow();
  });

  test("colours a message's delivery status in its own app, scoped to the field, and exports New message", () => {
    // Field-scoped: the generic words claim no global status tone another addon could need.
    expect(messaging.statusTones).not.toHaveProperty("queued");
    expect(messaging.vocabulary).toEqual([{ app: "messaging", resources: { "messaging.Message": { fields: { status: { tones: MESSAGE_STATUS_TONES } } } } }]);
    // What tells the states apart: Queued and Sent are not alarms, Uncertain is not Failed.
    expect(MESSAGE_STATUS_TONES).toEqual({ DRAFT: "neutral", QUEUED: "info", SENT: "success", UNCERTAIN: "warning" });
    // A scheduled draft does not read as Uncertain does.
    expect(statusTone("DRAFT", { ...MESSAGE_STATUS_TONES })).not.toBe(statusTone("UNCERTAIN", { ...MESSAGE_STATUS_TONES }));
    expect(statusTone("FAILED", { ...MESSAGE_STATUS_TONES })).toBe("danger");
    expect(statusTone("UNCERTAIN", { ...MESSAGE_STATUS_TONES })).toBe("warning");
    expect(messaging.icons).toHaveProperty("reply");
    expect(typeof NewMessageAction).toBe("function");
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
