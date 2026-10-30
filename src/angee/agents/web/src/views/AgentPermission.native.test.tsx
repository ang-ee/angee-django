// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { AppRuntimeProvider, ToastProvider, defaultWidgets } from "@angee/ui";

import { FakeAcpAgent } from "../acp-test-agent";
import { createAcpTestProviders, type AcpProviderOptions } from "../acp-test-providers";
import { AgentChat } from "./AgentChat";

const transport = vi.hoisted(() => ({ open: vi.fn() }));
vi.mock("../acp-transport", async (original) => ({
  ...await original<typeof import("../acp-transport")>(), openAcpTransport: transport.open,
}));

const fixtures: Array<() => void> = [];
afterEach(() => { cleanup(); fixtures.splice(0).forEach((dispose) => dispose()); });
const view = { kind: "dashboard", type: "agents/agent" } as const;

async function mount(version: 1 | 2, ask = true, providerOptions: AcpProviderOptions = {}) {
  const agent = new FakeAcpAgent(version);
  const providers = createAcpTestProviders(agent, version, providerOptions);
  fixtures.push(() => agent.close(), providers.clearClients);
  transport.open.mockImplementation(agent.open);
  render(<providers.Provider><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
    <AgentChat agentId="agt-1" view={view} runtimeClass="OPENCODE" />
  </AppRuntimeProvider></ToastProvider></providers.Provider>);
  const input = await screen.findByPlaceholderText("Message the agent…");
  fireEvent.change(input, { target: { value: "Use the tool" } });
  await waitFor(() => expect(screen.getByRole<HTMLButtonElement>("button", { name: "Send" }).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(agent.promptCalls).toBe(1));
  if (ask) {
    void agent.ask("s-1");
    await screen.findByRole("group", { name: "Tool permission" });
  }
  return agent;
}

describe.each([1, 2] as const)("ACP v%s permission UI", (version) => {
  test("holds the request until the user approves", async () => {
    const agent = await mount(version);
    expect(agent.permissions).toHaveLength(0);
    expect(screen.getByRole("alert").textContent).toBe("Allow the tool?");
    expect(screen.getByText(/record-1/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(agent.permissions).toEqual([{ outcome: { outcome: "selected", optionId: "approve" } }]));
    expect(screen.queryByRole("group", { name: "Tool permission" })).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByPlaceholderText("Message the agent…")));
  });

  test("rejects with a reason only on v2", async () => {
    const agent = await mount(version);
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    if (version === 1) {
      await waitFor(() => expect(agent.permissions).toEqual([{ outcome: { outcome: "selected", optionId: "reject" } }]));
      expect(screen.queryByRole("dialog")).toBeNull();
      return;
    }
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Reason (optional)" }), { target: { value: "Keep the record" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Reject" }));
    await waitFor(() => expect(agent.permissions).toEqual([{ outcome: {
      outcome: "selected", optionId: "reject", _meta: { angee: { reason: "Keep the record" } },
    } }]));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

test("v2 closes an open rejection dialog when another surface answers", async () => {
  const agent = await mount(2);
  fireEvent.click(screen.getByRole("button", { name: "Reject" }));
  await screen.findByRole("dialog");
  await act(async () => { await agent.state("s-1", "running"); });
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(screen.queryByRole("group", { name: "Tool permission" })).toBeNull();
  await waitFor(() => expect(document.activeElement).toBe(screen.getByPlaceholderText("Message the agent…")));
  await waitFor(() => expect(agent.permissions).toEqual([{ outcome: { outcome: "cancelled" } }]));
});

test("v1 closes an unanswered permission dialog when its prompt finishes elsewhere", async () => {
  const agent = await mount(1);
  await act(async () => { await agent.finish("s-1"); });
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(screen.queryByRole("group", { name: "Tool permission" })).toBeNull();
  await waitFor(() => expect(agent.permissions).toEqual([{ outcome: { outcome: "cancelled" } }]));
});

test("v2 composer shows Stop through prompt acceptance and requires_action, then Send after cancellation", async () => {
  const agent = await mount(2);
  expect(screen.getByRole("button", { name: "Stop" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Send" })).toBeTruthy();
  await act(async () => { await agent.state("s-1", "running"); });
  expect(screen.getByRole("button", { name: "Stop" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Stop" }));
  await screen.findByRole("button", { name: "Send" });
  await waitFor(() => expect(screen.queryByRole("button", { name: "Stop" })).toBeNull());
});

test.each(["Stored readable error", null])("v2 marks a failed turn without duplicating readable error text (%s)", async (errorText) => {
  const agent = await mount(2, false);
  await act(async () => { await agent.finish("s-1", "_angee/failed", errorText); });
  await screen.findByText("Failed");
  expect(screen.getAllByText(errorText ?? "The agent could not complete this turn. You can send another message.")).toHaveLength(1);
  if (errorText) expect(screen.queryByText("The agent could not complete this turn. You can send another message.")).toBeNull();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Try again" } });
  await waitFor(() => expect(screen.getByRole<HTMLButtonElement>("button", { name: "Send" }).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(agent.promptCalls).toBe(2));
});

test.each([1, 2] as const)("v%s keeps Current view removable and reattachable for each prompt", async (version) => {
  const agent = await mount(version, false, { renderPrompt: async () => "Rendered view" });
  await waitFor(() => expect(screen.queryByRole("button", { name: "Current view" })).toBeNull());
  if (version === 2) expect(agent.prompts[0]?._meta).toEqual({ angee: { context: view } });
  await act(async () => { await agent.finish("s-1"); });
  const attach = screen.getByRole<HTMLButtonElement>("button", { name: "Attach current view" });
  expect(attach.tagName).toBe("BUTTON");
  expect(attach.type).toBe("button");
  expect(attach.tabIndex).toBe(0);
  attach.focus();
  expect(document.activeElement).toBe(attach);
  expect(fireEvent.keyDown(attach, { key: "Enter", code: "Enter" })).toBe(true);
  fireEvent.keyUp(attach, { key: "Enter", code: "Enter" });
  // happy-dom has no native keyboard default action. A keyboard-activated
  // native button dispatches a click with detail=0 (Enter or Space).
  fireEvent.click(attach, { detail: 0 });
  expect(screen.getByRole("button", { name: "Current view" })).toBeTruthy();
  const remove = screen.getByRole<HTMLButtonElement>("button", { name: "Remove attachment" });
  expect(remove.type).toBe("button");
  expect(remove.tabIndex).toBe(0);
  remove.focus();
  expect(document.activeElement).toBe(remove);
  expect(fireEvent.keyDown(remove, { key: " ", code: "Space" })).toBe(true);
  fireEvent.keyUp(remove, { key: " ", code: "Space" });
  fireEvent.click(remove, { detail: 0 });
  expect(screen.queryByRole("button", { name: "Current view" })).toBeNull();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Without view" } });
  await waitFor(() => expect(screen.getByRole<HTMLButtonElement>("button", { name: "Send" }).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(agent.prompts).toHaveLength(2));
  expect(agent.prompts[1]?._meta).toBeUndefined();
  expect(agent.prompts[1]?.prompt).toEqual([{ type: "text", text: "Without view" }]);
  await act(async () => { await agent.finish("s-1"); });
  fireEvent.click(screen.getByRole("button", { name: "Attach current view" }), { detail: 0 });
  expect(screen.getByRole("button", { name: "Current view" })).toBeTruthy();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "With view again" } });
  await waitFor(() => expect(screen.getByRole<HTMLButtonElement>("button", { name: "Send" }).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(agent.prompts).toHaveLength(3));
  if (version === 2) expect(agent.prompts[2]?._meta).toEqual({ angee: { context: view } });
  else expect(agent.prompts[2]?.prompt).toEqual([{ type: "text", text: "Rendered view" }, { type: "text", text: "With view again" }]);
  await waitFor(() => expect(screen.queryByRole("button", { name: "Current view" })).toBeNull());
});

test("v2 Current view opens the existing context inspector without sending rendered context", async () => {
  const renderPrompt = vi.fn(async () => "<system_context>Inspected view</system_context>");
  const agent = await mount(2, false, { renderPrompt });
  expect(renderPrompt).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Attach current view" }));
  fireEvent.click(screen.getByRole("button", { name: "Current view" }));
  const dialog = await screen.findByRole("dialog");
  await waitFor(() => expect(dialog.textContent).toContain("Inspected view"));
  expect(renderPrompt).toHaveBeenCalledOnce();
  expect(agent.prompts[0]?.prompt).toEqual([{ type: "text", text: "Use the tool" }]);
});


test("v2 sends a queued prompt while Stop remains available", async () => {
  const agent = await mount(2, false);
  await act(async () => { await agent.chunk("s-1", "First stream"); });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Queue next" } });
  const send = screen.getByRole<HTMLButtonElement>("button", { name: "Send" });
  await waitFor(() => expect(send.disabled).toBe(false));
  fireEvent.click(send);
  await waitFor(() => expect(agent.promptCalls).toBe(2));
  expect(screen.getByRole("button", { name: "Stop" })).toBeTruthy();
  await act(async () => { await agent.finish("s-1"); await agent.chunk("s-1", "Queued answer"); await agent.finish("s-1"); });
  await screen.findByText("Queued answer");
});

test("v2 interleaved output has one assistant bubble and one Copy bar per turn", async () => {
  const agent = await mount(2, false);
  await act(async () => {
    await agent.chunk("s-1", "Before tool");
    await agent.update("s-1", { sessionUpdate: "agent_thought_chunk", messageId: "thought-1", content: { type: "text", text: "A thought" } });
    await agent.update("s-1", { sessionUpdate: "tool_call_update", toolCallId: "read-1", title: "Read record", status: "pending", rawInput: { id: "record-1" } });
    await agent.chunk("s-1", "After tool");
    await agent.update("s-1", { sessionUpdate: "tool_call_update", toolCallId: "read-1", status: "completed", rawOutput: "Done" });
    await agent.update("s-1", { sessionUpdate: "agent_thought_chunk", messageId: "thought-2", content: { type: "text", text: "Another thought" } });
    await agent.chunk("s-1", "After thought");
    await agent.finish("s-1");
  });
  await screen.findByText("After tool");
  const bubble = document.querySelector("[data-message-id=assistant-m-1]");
  expect(bubble).not.toBeNull();
  expect(screen.getAllByRole("button", { name: "Copy" })).toHaveLength(1);
  expect(within(bubble as HTMLElement).getByText("Before tool")).toBeTruthy();
  expect(within(bubble as HTMLElement).getByText("After tool")).toBeTruthy();
  expect(within(bubble as HTMLElement).getByText("After thought")).toBeTruthy();
  const segments = agent.sessions.get("s-1")?.v2History.flatMap(({ update }) => update.sessionUpdate === "agent_message_chunk" ? [update.messageId] : []) ?? [];
  expect(new Set(segments).size).toBe(3);
  const thoughts = agent.sessions.get("s-1")?.v2History.flatMap(({ update }) => update.sessionUpdate === "agent_thought_chunk" ? [update.messageId] : []) ?? [];
  expect(new Set(thoughts).size).toBe(2);
});


test("v1 retains a new draft while its single prompt is running", async () => {
  const agent = await mount(1, false);
  const composer = screen.getByRole<HTMLTextAreaElement>("textbox");
  fireEvent.change(composer, { target: { value: "Keep unsent draft" } });
  fireEvent.keyDown(composer, { key: "Enter" });
  expect(composer.value).toBe("Keep unsent draft");
  expect(agent.promptCalls).toBe(1);
  expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
  await act(async () => { await agent.finish("s-1"); });
  await screen.findByRole("button", { name: "Send" });
  expect(composer.value).toBe("Keep unsent draft");
});
