import { describe, expect, it } from "vitest";
import type { SessionNotification } from "@agentclientprotocol/sdk";

import { foldIntoLog, type ChatMessage, type ChatPart } from "./acp-log";

// Build a `session/update` notification from a bare update payload. The ACP update union
// is wide; the reducer only reads the fields below, so the tests cast at this boundary.
function note(update: Record<string, unknown>): SessionNotification {
  return { sessionId: "s1", update } as unknown as SessionNotification;
}

function toolParts(message: ChatMessage | undefined): Extract<ChatPart, { kind: "tool" }>[] {
  return (message?.parts ?? []).filter(
    (part): part is Extract<ChatPart, { kind: "tool" }> => part.kind === "tool",
  );
}

describe("foldIntoLog", () => {
  it("derives a stable assistant id from the persisted turn user id", () => {
    const initial: ChatMessage[] = [
      { id: "user-atn_1", role: "user", parts: [{ kind: "text", text: "Hello" }] },
    ];
    const first = foldIntoLog(
      initial,
      note({ sessionUpdate: "agent_message_chunk", content: { type: "text", text: "Answer" } }),
    );
    const refolded = foldIntoLog(
      [{ id: "user-atn_1", role: "user", parts: [{ kind: "text", text: "Hello" }] }],
      note({ sessionUpdate: "agent_message_chunk", content: { type: "text", text: "Answer" } }),
    );

    expect(first[1]?.id).toBe("assistant-atn_1");
    expect(refolded[1]?.id).toBe(first[1]?.id);
  });

  it("coalesces a run of agent_message_chunk text into one part", () => {
    let log: ChatMessage[] = [];
    log = foldIntoLog(log, note({ sessionUpdate: "agent_message_chunk", content: { type: "text", text: "Hello" } }));
    log = foldIntoLog(log, note({ sessionUpdate: "agent_message_chunk", content: { type: "text", text: " world" } }));
    expect(log).toHaveLength(1);
    expect(log[0]?.parts).toEqual([{ kind: "text", text: "Hello world" }]);
  });

  // Regression: Claude Code re-emits `tool_call` for the same id as the input streams in.
  // Appending (rather than upserting) produced two parts with the same toolCallId, which
  // crashed assistant-ui with "Duplicate key toolCallId-… in tapResources".
  it("upserts a tool_call re-sent with the same id into a single part", () => {
    let log: ChatMessage[] = [];
    log = foldIntoLog(log, note({ sessionUpdate: "tool_call", toolCallId: "t1", title: "read_note", status: "pending", rawInput: {}, content: [] }));
    log = foldIntoLog(log, note({ sessionUpdate: "tool_call", toolCallId: "t1", title: "read_note", status: "pending", rawInput: { id: "n1" }, content: [] }));
    const tools = toolParts(log[0]);
    expect(tools).toHaveLength(1);
    expect(tools[0]).toMatchObject({ id: "t1", toolName: "read_note", input: { id: "n1" } });
  });

  it("applies a tool_call_update onto the matching part (status, result, isError)", () => {
    let log: ChatMessage[] = [];
    log = foldIntoLog(log, note({ sessionUpdate: "tool_call", toolCallId: "t1", title: "read_note", status: "pending", rawInput: { id: "n1" } }));
    log = foldIntoLog(log, note({ sessionUpdate: "tool_call_update", toolCallId: "t1", status: "failed", rawOutput: "<tool_use_error>nope</tool_use_error>" }));
    const tools = toolParts(log[0]);
    expect(tools).toHaveLength(1);
    expect(tools[0]).toMatchObject({
      toolName: "read_note",
      status: "failed",
      isError: true,
      result: "<tool_use_error>nope</tool_use_error>",
    });
  });

  it("ignores a tool_call_update for an unknown id and returns the log unchanged", () => {
    const log: ChatMessage[] = [];
    const next = foldIntoLog(log, note({ sessionUpdate: "tool_call_update", toolCallId: "ghost", status: "completed" }));
    expect(next).toBe(log);
  });

  // Latent session state (slash commands, plan, mode) is NOT a transcript part: the transcript
  // reducer drops it and `foldIntoSession` (acp-session.ts) owns it. Returning the same log ref
  // proves the ownership split and avoids a needless re-render.
  it("drops available_commands_update — it is session state, not a transcript part", () => {
    const log: ChatMessage[] = [];
    const next = foldIntoLog(
      log,
      note({ sessionUpdate: "available_commands_update", availableCommands: [{ name: "summarize", description: "Summarize" }] }),
    );
    expect(next).toBe(log);
  });
});
