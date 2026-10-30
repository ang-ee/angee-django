import { describe, expect, test } from "vitest";

import { agentChatViewInput } from "./documents";

describe("agentChatViewInput", () => {
  test("keeps the typed view envelope and drops invalid optional JSON", () => {
    expect(agentChatViewInput({
      kind: "record",
      type: "notes.Note",
      sqid: "nte_1",
      params: { invalid: new Date("2026-01-01") },
    })).toEqual({
      kind: "record",
      type: "notes.Note",
      sqid: "nte_1",
    });
  });
});
