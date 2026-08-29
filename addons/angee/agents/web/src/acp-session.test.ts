import { describe, expect, it } from "vitest";
import type { SessionNotification } from "@agentclientprotocol/sdk";

import { emptySession, foldIntoSession, type AcpSession } from "./acp-session";

// Build a `session/update` notification from a bare update payload. The ACP update union is
// wide; the reducer only reads the fields below, so the tests cast at this boundary.
function note(update: Record<string, unknown>): SessionNotification {
  return { sessionId: "s1", update } as unknown as SessionNotification;
}

describe("foldIntoSession", () => {
  it("populates availableCommands from an available_commands_update", () => {
    const next = foldIntoSession(
      emptySession,
      note({
        sessionUpdate: "available_commands_update",
        availableCommands: [{ name: "summarize", description: "Summarize the note" }],
      }),
    );
    expect(next.availableCommands).toEqual([{ name: "summarize", description: "Summarize the note" }]);
  });

  it("replaces (not appends) the command list on a second update", () => {
    let session: AcpSession = foldIntoSession(
      emptySession,
      note({
        sessionUpdate: "available_commands_update",
        availableCommands: [{ name: "summarize", description: "Summarize the note" }],
      }),
    );
    session = foldIntoSession(
      session,
      note({
        sessionUpdate: "available_commands_update",
        availableCommands: [{ name: "translate", description: "Translate the note" }],
      }),
    );
    expect(session.availableCommands).toEqual([{ name: "translate", description: "Translate the note" }]);
  });

  it("returns the same reference for an unrelated update", () => {
    const session = foldIntoSession(
      emptySession,
      note({
        sessionUpdate: "available_commands_update",
        availableCommands: [{ name: "summarize", description: "Summarize" }],
      }),
    );
    const next = foldIntoSession(
      session,
      note({ sessionUpdate: "agent_message_chunk", content: { type: "text", text: "hi" } }),
    );
    expect(next).toBe(session);
  });

  it("starts with an empty command list", () => {
    expect(emptySession.availableCommands).toEqual([]);
  });
});
