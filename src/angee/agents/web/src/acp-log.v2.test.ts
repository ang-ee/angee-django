import { describe, expect, test } from "vitest";
import type { UpdateSessionNotification } from "@agentclientprotocol/sdk/experimental/v2";

import { foldIntoLog, reconcileUserMessage, type ChatMessage } from "./acp-log";
import type { AcpNotification } from "./acp-client";

function note(update: UpdateSessionNotification["update"]): AcpNotification {
  return { protocolVersion: 2, params: { sessionId: "s1", update } };
}

function startTurn(): ChatMessage[] {
  const log = foldIntoLog([], note({ sessionUpdate: "user_message", messageId: "u1", content: [{ type: "text", text: "Hello" }] }));
  return foldIntoLog(log, note({ sessionUpdate: "state_update", state: "running" }));
}

describe("ACP v2 transcript", () => {
  test("upserts by messageId with replacement, append, omission and null semantics", () => {
    let log = startTurn();
    log = foldIntoLog(log, note({ sessionUpdate: "agent_message_chunk", messageId: "a1", content: { type: "text", text: "Draft" } }));
    log = foldIntoLog(log, note({ sessionUpdate: "agent_message", messageId: "a1", content: [{ type: "text", text: "Final" }] }));
    log = foldIntoLog(log, note({ sessionUpdate: "agent_message_chunk", messageId: "a1", content: { type: "text", text: " answer" } }));
    log = foldIntoLog(log, note({ sessionUpdate: "agent_message", messageId: "a1" }));
    expect(log).toHaveLength(2);
    expect(log[1]?.parts).toEqual([{ kind: "text", text: "Final answer", messageId: "a1" }]);
    log = foldIntoLog(log, note({ sessionUpdate: "agent_message", messageId: "a1", content: null }));
    expect(log[1]?.parts).toEqual([]);
  });

  test("keeps tool patches and content chunks through replacement of an assistant message", () => {
    let log = startTurn();
    log = foldIntoLog(log, note({ sessionUpdate: "agent_message", messageId: "a1", content: [{ type: "text", text: "Working" }] }));
    log = foldIntoLog(log, note({ sessionUpdate: "tool_call_update", toolCallId: "t1", title: "Read", rawInput: { id: "r1" }, status: "failed" }));
    const content = { type: "content", content: { type: "text", text: "Tool output" } } as const;
    log = foldIntoLog(log, note({ sessionUpdate: "tool_call_content_chunk", toolCallId: "t1", content }));
    log = foldIntoLog(log, note({ sessionUpdate: "tool_call_update", toolCallId: "t1", status: "completed" }));
    log = foldIntoLog(log, note({ sessionUpdate: "agent_message", messageId: "a1", content: [{ type: "text", text: "Done" }] }));
    expect(log).toHaveLength(2);
    expect(log[1]?.parts[1]).toMatchObject({ toolName: "Read", input: { id: "r1" }, status: "completed", isError: false, content: [content] });
    log = foldIntoLog(log, note({ sessionUpdate: "tool_call_update", toolCallId: "t1", title: null, rawInput: null, content: null }));
    expect(log[1]?.parts[1]).toMatchObject({ toolName: "", input: null, content: null });
  });

  test("replays user images and thought messages with authoritative ids", () => {
    let log = foldIntoLog([], note({ sessionUpdate: "user_message", messageId: "u1", content: [{ type: "image", data: "AAA", mimeType: "image/png" }] }));
    log = foldIntoLog(log, note({ sessionUpdate: "state_update", state: "running" }));
    log = foldIntoLog(log, note({ sessionUpdate: "agent_thought_chunk", messageId: "r1", content: { type: "text", text: "Thinking" } }));
    expect(log[0]).toMatchObject({ id: "u1", role: "user", parts: [{ kind: "image", image: "data:image/png;base64,AAA" }] });
    expect(log[1]).toMatchObject({ id: "assistant-u1", role: "assistant", parts: [{ kind: "reasoning", text: "Thinking" }] });
  });

  test("shows a failed turn with no output once, including repeated idle snapshots", () => {
    const failed = note({ sessionUpdate: "state_update", state: "idle", stopReason: "_angee/failed" });
    let log = startTurn();
    log = foldIntoLog(log, failed, "Translated failure");
    log = foldIntoLog(log, failed, "Translated failure");
    expect(log).toHaveLength(2);
    expect(log[1]).toMatchObject({ status: { type: "incomplete", reason: "error" }, parts: [{ kind: "text", text: "Translated failure" }] });
  });

  test("marks the server's readable failure message without appending generic text", () => {
    let log = startTurn();
    log = foldIntoLog(log, note({ sessionUpdate: "agent_message_chunk", messageId: "partial", content: { type: "text", text: "Partial answer" } }));
    log = foldIntoLog(log, note({ sessionUpdate: "agent_message", messageId: "opaque-error", content: [{ type: "text", text: "Stored readable error" }] }));
    log = foldIntoLog(log, note({ sessionUpdate: "state_update", state: "idle", stopReason: "_angee/failed" }), "Translated failure");
    expect(log[1]).toMatchObject({ failed: true, status: { type: "incomplete", reason: "error" }, parts: [
      { kind: "text", text: "Partial answer" }, { kind: "text", text: "Stored readable error" },
    ] });
  });

  test.each(["partial", "empty", "thought", "tool"] as const)("falls back when the failed turn has no preceding readable message (%s)", (output) => {
    let log = startTurn();
    const update: UpdateSessionNotification["update"] = output === "partial"
      ? { sessionUpdate: "agent_message_chunk", messageId: "partial", content: { type: "text", text: "Partial answer" } }
      : output === "empty" ? { sessionUpdate: "agent_message", messageId: "empty", content: [{ type: "text", text: " " }] }
      : output === "thought" ? { sessionUpdate: "agent_thought", messageId: "thought", content: [{ type: "text", text: "Thinking" }] }
      : { sessionUpdate: "tool_call_update", toolCallId: "tool", title: "Read", status: "completed" };
    log = foldIntoLog(log, note(update));
    log = foldIntoLog(log, note({ sessionUpdate: "state_update", state: "idle", stopReason: "_angee/failed" }), "Translated failure");
    expect(log[1]?.parts.at(-1)).toEqual({ kind: "text", text: "Translated failure" });
    expect(log[1]?.failed).toBe(true);
  });

  test("ignores unknown future update variants through the SDK guards", () => {
    const log: ChatMessage[] = [];
    expect(foldIntoLog(log, note({ sessionUpdate: "_future/update", content: "opaque" }))).toBe(log);
  });
});


test("v2 folds text, thoughts, tools and later text in arrival order within one turn", () => {
  let log = foldIntoLog([], note({ sessionUpdate: "user_message", messageId: "u1", content: [{ type: "text", text: "Hello" }] }));
  for (const value of [
    { sessionUpdate: "state_update", state: "running" },
    { sessionUpdate: "agent_message_chunk", messageId: "a1", content: { type: "text", text: "Before" } },
    { sessionUpdate: "agent_thought_chunk", messageId: "thought", content: { type: "text", text: "Think" } },
    { sessionUpdate: "tool_call_update", toolCallId: "tool", title: "Read", rawInput: { id: "r1" } },
    { sessionUpdate: "agent_message_chunk", messageId: "a2", content: { type: "text", text: "After" } },
    { sessionUpdate: "tool_call_update", toolCallId: "tool", status: "completed", rawOutput: "Result" },
  ] satisfies Array<UpdateSessionNotification["update"]>) log = foldIntoLog(log, note(value));
  expect(log).toHaveLength(2);
  expect(log[1]?.parts).toMatchObject([
    { kind: "text", text: "Before" }, { kind: "reasoning", text: "Think" },
    { kind: "tool", toolName: "Read", status: "completed", input: { id: "r1" }, result: "Result" },
    { kind: "text", text: "After" },
  ]);
});

test.each(["end_turn", "_angee/failed"])("v2 settles an empty foreground turn once (%s), then starts the queued turn", (stopReason) => {
  let log = startTurn();
  log = foldIntoLog(log, note({ sessionUpdate: "user_message", messageId: "u2", content: [{ type: "text", text: "Queued" }] }));
  const idle = note({ sessionUpdate: "state_update", state: "idle", stopReason });
  log = foldIntoLog(log, idle, "Translated failure");
  expect(log[1]?.status?.type).toBe(stopReason === "end_turn" ? "complete" : "incomplete");
  expect(foldIntoLog(log, idle, "Translated failure")).toBe(log);
  expect(log.some((message) => message.turnId === "u2")).toBe(false);
  log = foldIntoLog(log, note({ sessionUpdate: "state_update", state: "running" }));
  log = foldIntoLog(log, note({ sessionUpdate: "agent_message_chunk", messageId: "opaque-output", content: { type: "text", text: "Second answer" } }));
  log = foldIntoLog(log, note({ sessionUpdate: "state_update", state: "idle", stopReason: "end_turn" }));
  expect(log[3]).toMatchObject({ turnId: "u2", status: { type: "complete" }, parts: [{ text: "Second answer" }] });
});

test("v2 starts the oldest accepted turn, skipping unsent optimistic and failed messages", () => {
  const log: ChatMessage[] = [
    { id: "local-failed", role: "user", deliveryFailed: true, parts: [] },
    { id: "local-pending", role: "user", optimistic: true, parts: [] },
    { id: "oldest", role: "user", parts: [] },
    { id: "latest", role: "user", parts: [] },
  ];
  const running = foldIntoLog(log, note({ sessionUpdate: "state_update", state: "running" }));
  expect(running[3]).toMatchObject({ turnId: "oldest", status: { type: "running" } });
  const resumed = foldIntoLog(running, note({ sessionUpdate: "state_update", state: "requires_action" }));
  expect(foldIntoLog(resumed, note({ sessionUpdate: "state_update", state: "running" }))).toEqual(running);
});

test("v2 uses echo acceptance order when another tab queues before a local optimistic send", () => {
  let log = startTurn();
  log.push({ id: "local", role: "user", optimistic: true, parts: [{ kind: "text", text: "Local draft" }] });
  log = foldIntoLog(log, note({ sessionUpdate: "user_message", messageId: "other-tab", content: [{ type: "text", text: "Accepted first" }] }));
  log = reconcileUserMessage(log, "local", "local-accepted");
  log = foldIntoLog(log, note({ sessionUpdate: "user_message", messageId: "local-accepted", content: [{ type: "text", text: "Local draft" }] }));
  expect(log.filter((message) => message.role === "user").map((message) => message.id)).toEqual(["u1", "other-tab", "local-accepted"]);
  log = foldIntoLog(log, note({ sessionUpdate: "state_update", state: "idle", stopReason: "end_turn" }));
  log = foldIntoLog(log, note({ sessionUpdate: "state_update", state: "running" }));
  expect(log.find((message) => message.status?.type === "running")?.turnId).toBe("other-tab");
});
