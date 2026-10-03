// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { AppRuntimeProvider } from "@angee/ui";
import { afterEach, describe, expect, test, vi } from "vitest";
import { FakeAcpAgent } from "../acp-test-agent";
import { createAcpTestProviders } from "../acp-test-providers";

import type { AgentRosterItem } from "../documents";
import { AgentChat } from "./AgentChat";

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

const props = {
  agentId: "agt_1",
  sessionId: "ase_1",
  runtimeClass: "EXTERNAL",
  view: { kind: "record", type: "notes/note", sqid: "nte_1" },
} as const;

describe("agent chat composition", () => {
  test("an explicit protocol the agent does not speak fails clearly", async () => {
    const Provider = native();
    render(<Provider><AppRuntimeProvider runtime={{}}><AgentChat {...props} protocolVersion={2} /></AppRuntimeProvider></Provider>);
    expect(await screen.findByText("This agent uses an unsupported chat protocol.")).toBeTruthy();
  });
  test("uses the default ACP surface when no transport contribution exists", async () => {
    const Provider = native();
    render(<Provider><AppRuntimeProvider runtime={{}}><AgentChat {...props} sessionId={undefined} /></AppRuntimeProvider></Provider>);
    expect(await screen.findByRole("textbox")).toBeTruthy();
    expect(screen.queryByText("Chat is unavailable for this agent.")).toBeNull();
  });

  test("every runtime chats over ACP, with the chooser in the bar", async () => {
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
      <Provider><AppRuntimeProvider runtime={{}}>
        <Switcher />
      </AppRuntimeProvider></Provider>,
    );
    fireEvent.click(await screen.findByRole("combobox", { name: "Switch agent" }));
    const option = screen.getByRole("option", { name: /opencode/ });
    fireEvent.pointerDown(option);
    fireEvent.pointerUp(option);
    fireEvent.click(option);
    expect((await screen.findByRole("combobox", { name: "Switch agent" })).textContent).toContain("opencode");
    expect(await screen.findByRole("textbox")).toBeTruthy();
    expect(screen.queryByText("Chat is unavailable for this agent.")).toBeNull();
  });
});
