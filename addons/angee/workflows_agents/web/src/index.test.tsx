import { AGENT_SESSION_SLOT } from "@angee/agents";
import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import workflowsAgents from "./index";

describe("workflows-agents web manifest", () => {
  test("contributes one typed persisted-session approval surface", () => {
    expect(() => expectValidBaseAddon(workflowsAgents)).not.toThrow();
    expect((workflowsAgents.slots ?? []).map((entry) => [entry.slot, entry.id])).toEqual([
      [AGENT_SESSION_SLOT, "workflows-agents.approvals"],
    ]);
  });
});
