import { expectValidBaseAddon } from "@angee/app/testing";
import type { BaseMenuItem } from "@angee/ui";
import { describe, expect, test } from "vitest";

import agents from "./index";

describe("agents addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(agents)).not.toThrow();
  });

  test("registers the session route pair and the record aside's chat tab", () => {
    const sessionRoute = agents.routes?.find((route) => route.name === "agents.session");
    expect(sessionRoute?.parent).toBe("agents.sessions");
    expect(sessionRoute?.path).toBe("/agents/sessions/$id");
    // Every record view's aside; `?chatterTab=agents` links keep resolving through the alias.
    expect(agents.containers?.["record#aside"]).toMatchObject({
      "agents.chat": { sequence: 0, content: { label: "Agents", icon: "agent", aliases: ["agents"] } },
    });
  });


  test("owns separate Agents and AI menu roots without changing inference routes", () => {
    const [agentsRoot, aiRoot] = (agents.menus ?? []) as readonly BaseMenuItem[];

    expect((agentsRoot?.children ?? []).map((item) => item.id)).toEqual([
      "agents.menu.agents",
      "agents.menu.skills",
      "agents.menu.mcp",
    ]);
    expect(aiRoot).toMatchObject({
      id: "agents.ai",
      label: "AI",
      icon: "inference-provider",
      group: "platform",
    });
    expect(aiRoot?.children?.map((item) => item.route)).toEqual([
      "agents.providers",
      "agents.models",
    ]);
  });
});
