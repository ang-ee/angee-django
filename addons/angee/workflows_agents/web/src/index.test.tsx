import { AGENT_SESSION_SLOT } from "@angee/agents";
import { AGENT_CHAT_SLOT } from "@angee/agents/chat";
import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import workflowsAgents from "./index";

describe("workflows-agents web manifest", () => {
  test("contributes persisted chat for its runtime and session approvals", () => {
    expect(() => expectValidBaseAddon(workflowsAgents)).not.toThrow();
    expect((workflowsAgents.slots ?? []).map(({ slot, model, impl, id }) => ({ slot, model, impl, id }))).toEqual([
      { slot: AGENT_CHAT_SLOT, model: "agents.Agent", impl: "pydantic", id: "chat" },
      { slot: AGENT_SESSION_SLOT, id: "workflows-agents.approvals" },
    ]);
  });
});
