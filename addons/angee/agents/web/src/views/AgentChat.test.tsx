// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { AppRuntimeProvider } from "@angee/ui";
import { afterEach, describe, expect, test } from "vitest";

import { AGENT_CHAT_SLOT, useAgentChatContext } from "../chat-slot";
import { AgentChat } from "./AgentChat";

afterEach(cleanup);

function ContributedChat() {
  const { agentId, sessionId, view } = useAgentChatContext();
  return <output>{`${agentId}:${sessionId}:${view.sqid}`}</output>;
}

const props = {
  agentId: "agt_1",
  sessionId: "ase_1",
  runtimeClass: "PYDANTIC",
  view: { kind: "record", type: "notes/note", sqid: "nte_1" },
} as const;

describe("agent chat composition", () => {
  test("does not start a transport when its optional addon is absent", () => {
    render(<AppRuntimeProvider runtime={{}}><AgentChat {...props} /></AppRuntimeProvider>);
    expect(screen.getByText("Chat is unavailable for this agent.")).toBeTruthy();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  test("selects only the matching runtime contribution and binds the current agent", () => {
    const runtime = {
      slots: [
        { slot: AGENT_CHAT_SLOT, model: "agents.Agent", impl: "pydantic", id: "chat", content: <ContributedChat /> },
        { slot: AGENT_CHAT_SLOT, model: "agents.Agent", impl: "opencode", id: "chat", content: "Other runtime" },
      ],
    };
    const { rerender } = render(
      <AppRuntimeProvider runtime={runtime}><AgentChat {...props} /></AppRuntimeProvider>,
    );
    expect(screen.getByText("agt_1:ase_1:nte_1")).toBeTruthy();
    expect(screen.queryByText("Other runtime")).toBeNull();
    rerender(
      <AppRuntimeProvider runtime={runtime}>
        <AgentChat {...props} agentId="agt_2" sessionId="ase_2" />
      </AppRuntimeProvider>,
    );
    expect(screen.getByText("agt_2:ase_2:nte_1")).toBeTruthy();
  });

  test("keeps the chooser available to leave an unsupported runtime", () => {
    const agents = ["pydantic", "opencode"].map((runtime) => ({
      id: runtime, name: runtime, runtime_class: runtime, runtime_status: "RUNNING" as const,
      is_template: false, updated_at: "2026-09-21", model: null,
    }));
    function Switcher() {
      const [selected, select] = useState("pydantic");
      return <AgentChat {...props} agentId={selected} runtimeClass={selected} agents={agents} onSelectAgent={select} />;
    }
    render(
      <AppRuntimeProvider runtime={{ slots: [{
        slot: AGENT_CHAT_SLOT, model: "agents.Agent", impl: "opencode", id: "chat", content: <ContributedChat />,
      }] }}>
        <Switcher />
      </AppRuntimeProvider>,
    );
    fireEvent.click(screen.getByRole("combobox", { name: "Switch agent" }));
    const option = screen.getByRole("option", { name: /opencode/ });
    fireEvent.pointerDown(option);
    fireEvent.pointerUp(option);
    fireEvent.click(option);
    expect(screen.getByText("opencode:ase_1:nte_1")).toBeTruthy();
    expect(screen.queryByText("Chat is unavailable for this agent.")).toBeNull();
  });
});
