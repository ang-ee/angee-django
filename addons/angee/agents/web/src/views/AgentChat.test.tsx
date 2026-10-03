// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { AppRuntimeProvider } from "@angee/ui";
import { afterEach, describe, expect, test, vi } from "vitest";
import { FakeAcpAgent } from "../acp-test-agent";
import { createAcpTestProviders } from "../acp-test-providers";

import { AGENT_CHAT_SLOT, useAgentChatContext } from "../chat-slot";
import type { AgentRosterItem } from "../documents";
import { AcpAgentChat, AgentChat } from "./AgentChat";

const transport = vi.hoisted(() => ({ open: vi.fn() }));
vi.mock("../acp-transport", async (original) => ({
  ...await original<typeof import("../acp-transport")>(), openAcpTransport: transport.open,
}));
const disposals: Array<() => void> = [];
afterEach(() => { cleanup(); disposals.splice(0).forEach((dispose) => dispose()); });

function native() {
  const agent = new FakeAcpAgent(1);
  const providers = createAcpTestProviders(agent);
  disposals.push(() => agent.close(), providers.clearClients);
  transport.open.mockImplementation(agent.open);
  return providers.Provider;
}

function ContributedChat() {
  const { agentId, sessionId, view } = useAgentChatContext();
  return <output>{`${agentId}:${sessionId}:${view.sqid}`}</output>;
}

const props = {
  agentId: "agt_1",
  sessionId: "ase_1",
  runtimeClass: "EXTERNAL",
  view: { kind: "record", type: "notes/note", sqid: "nte_1" },
} as const;

describe("agent chat composition", () => {
  test("Settings opened from conversation options returns focus there on Escape", async () => {
    const Provider = native();
    render(<Provider><AppRuntimeProvider runtime={{}}><AgentChat {...props} sessionId={undefined} /></AppRuntimeProvider></Provider>);
    const options = await screen.findByRole("button", { name: "Conversation options" });
    fireEvent.click(options);
    fireEvent.click(await screen.findByRole("menuitem", { name: "Session settings" }));
    const dialog = await screen.findByRole("dialog", { name: "Session settings" });
    await waitFor(() => expect(screen.queryByRole("menu")).toBeNull());
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(options));
  });

  test("a runtime slot can supply an explicit protocol and disagreement fails clearly", async () => {
    const Provider = native();
    render(<Provider><AppRuntimeProvider runtime={{ slots: [{
      slot: AGENT_CHAT_SLOT, model: "agents.Agent", impl: "external", id: "chat", content: <AcpAgentChat protocolVersion={2} />,
    }] }}><AgentChat {...props} /></AppRuntimeProvider></Provider>);
    expect(await screen.findByText("This agent uses an unsupported chat protocol.")).toBeTruthy();
  });
  test("uses the default ACP surface when no transport contribution exists", async () => {
    const Provider = native();
    render(<Provider><AppRuntimeProvider runtime={{}}><AgentChat {...props} sessionId={undefined} /></AppRuntimeProvider></Provider>);
    expect(await screen.findByRole("textbox")).toBeTruthy();
    expect(screen.queryByText("Chat is unavailable for this agent.")).toBeNull();
  });

  test("selects only the matching runtime contribution and binds the current agent", () => {
    const runtime = {
      slots: [
        { slot: AGENT_CHAT_SLOT, model: "agents.Agent", impl: "external", id: "chat", content: <ContributedChat /> },
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

  test("keeps the chooser available alongside the default ACP transport", async () => {
    const Provider = native();
    const agents: AgentRosterItem[] = ["external", "opencode"].map((runtime) => ({
      id: runtime, name: runtime, runtime_class: runtime as AgentRosterItem["runtime_class"],
      runtime_status: "RUNNING" as const, is_template: false, updated_at: "2026-09-21", model: null,
    }));
    function Switcher() {
      const [selected, select] = useState("external");
      return <AgentChat {...props} sessionId={undefined} agentId={selected} runtimeClass={selected} agents={agents} onSelectAgent={select} />;
    }
    render(
      <Provider><AppRuntimeProvider runtime={{ slots: [{
        slot: AGENT_CHAT_SLOT, model: "agents.Agent", impl: "opencode", id: "chat", content: <ContributedChat />,
      }] }}>
        <Switcher />
      </AppRuntimeProvider></Provider>,
    );
    fireEvent.click(await screen.findByRole("combobox", { name: "Switch agent" }));
    const option = screen.getByRole("option", { name: /opencode/ });
    fireEvent.pointerDown(option);
    fireEvent.pointerUp(option);
    fireEvent.click(option);
    expect(screen.getByText("opencode:undefined:nte_1")).toBeTruthy();
    expect(screen.queryByText("Chat is unavailable for this agent.")).toBeNull();
  });
});
