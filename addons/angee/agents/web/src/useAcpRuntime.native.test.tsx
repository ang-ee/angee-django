// @vitest-environment happy-dom

import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { FakeAcpAgent } from "./acp-test-agent";
import { createAcpTestProviders, type AcpProviderOptions } from "./acp-test-providers";
import { useAcpRuntime, type AcpRuntimeOptions } from "./useAcpRuntime";

const transport = vi.hoisted(() => ({ open: vi.fn() }));
vi.mock("./acp-transport", async (original) => ({
  ...await original<typeof import("./acp-transport")>(), openAcpTransport: transport.open,
}));

const fixtures: Array<{ close: () => void }> = [];
afterEach(() => { cleanup(); fixtures.splice(0).forEach((fixture) => fixture.close()); vi.restoreAllMocks(); vi.useRealTimers(); });
const view = { kind: "dashboard", type: "agents/agent" } as const;

async function mount(agent: FakeAcpAgent, sessionId?: string, protocolVersion: number = agent.version, options: Partial<AcpRuntimeOptions> = {}, providerOptions: AcpProviderOptions = {}) {
  const providers = createAcpTestProviders(agent, protocolVersion, providerOptions);
  fixtures.push(agent, { close: providers.clearClients });
  transport.open.mockImplementation(agent.open);
  const hook = renderHook(() => useAcpRuntime({ agentId: "agt-1", view, knownSessionId: sessionId, ...options }), { wrapper: providers.Provider });
  await waitFor(() => expect(hook.result.current.status).toBe(protocolVersion === 1 || protocolVersion === 2 ? "ready" : "error"), { timeout: 5000 });
  return hook;
}

function text(hook: Awaited<ReturnType<typeof mount>>): string {
  return hook.result.current.runtime.thread.getState().messages.flatMap((message) => message.content.map((part) => part.type === "text" ? part.text : "")).join("|");
}

async function send(hook: Awaited<ReturnType<typeof mount>>, agent: FakeAcpAgent, message = "Hello") {
  const before = agent.promptCalls;
  act(() => hook.result.current.runtime.thread.append(message));
  await waitFor(() => expect(agent.promptCalls).toBe(before + 1));
}

describe("ACP v2 runtime", () => {
  test("prompt acceptance keeps Stop active until streamed work becomes idle", async () => {
    const agent = new FakeAcpAgent(2);
    const hook = await mount(agent);
    await send(hook, agent);
    await waitFor(() => expect(hook.result.current.runtime.thread.getState().isRunning).toBe(true));
    expect(agent.initialized).toEqual([2]);
    await act(async () => { await agent.chunk("s-1", "First "); await agent.chunk("s-1", "reply"); });
    await waitFor(() => expect(text(hook)).toContain("First reply"));
    await act(async () => { await agent.finish("s-1"); });
    await waitFor(() => expect(hook.result.current.runtime.thread.getState().isRunning).toBe(false));
    await send(hook, agent, "Another message");
  });

  test.each([true, false])("reconciles the optimistic message by messageId (echo before response: %s)", async (echoBeforeResponse) => {
    const agent = new FakeAcpAgent(2); agent.echoBeforeResponse = echoBeforeResponse;
    const hook = await mount(agent);
    await send(hook, agent);
    await waitFor(() => {
      const users = hook.result.current.runtime.thread.getState().messages.filter((message) => message.role === "user");
      expect(users).toHaveLength(1);
      expect(users[0]?.id).toBe("m-1");
      expect(users[0]?.content).toEqual([{ type: "text", text: "Hello" }]);
    });
  });

  test("Stop waits for idle and settles the transcript as cancelled", async () => {
    const agent = new FakeAcpAgent(2);
    const hook = await mount(agent);
    await send(hook, agent);
    await act(async () => { await agent.chunk("s-1", "Partial"); });
    act(() => hook.result.current.runtime.thread.cancelRun());
    await waitFor(() => expect(hook.result.current.runtime.thread.getState().isRunning).toBe(false));
    await waitFor(() => expect(hook.result.current.runtime.thread.getState().messages.at(-1)?.status).toEqual({ type: "incomplete", reason: "cancelled" }));
  });

  test.each(["mid-turn", "completed", "failed"] as const)("reconnect %s resumes the same session with replay from start", async (when) => {
    const agent = new FakeAcpAgent(2);
    const hook = await mount(agent);
    await send(hook, agent);
    await act(async () => { await agent.chunk("s-1", "Before"); });
    act(() => agent.opened[0]?.close());
    if (when !== "mid-turn") await act(async () => { await agent.finish("s-1", when === "failed" ? "_angee/failed" : "end_turn"); });
    if (when !== "mid-turn") act(() => hook.result.current.reconnect());
    await waitFor(() => expect(agent.restored).toHaveLength(1), { timeout: 3000 });
    await waitFor(() => expect(hook.result.current.status).toBe("ready"));
    expect(agent.sessions.size).toBe(1);
    expect(agent.restored[0]).toEqual({ method: "session/resume", sessionId: "s-1", replayFrom: { type: "start" } });
    expect(text(hook).match(/Before/g)).toHaveLength(1);
    if (when === "mid-turn") {
      expect(hook.result.current.runtime.thread.getState().isRunning).toBe(true);
      await act(async () => { await agent.chunk("s-1", " after"); await agent.finish("s-1"); });
      await waitFor(() => expect(text(hook)).toContain("Before after"));
    }
    await waitFor(() => expect(hook.result.current.runtime.thread.getState().isRunning).toBe(false));
    if (when === "failed") {
      expect(text(hook).match(/Stored readable error/g)).toHaveLength(1);
      expect(text(hook)).not.toContain("The agent could not complete this turn.");
    }
    await send(hook, agent, "Try again");
  });

  test("mounting another tab resumes the known session, follows live work, and can Stop", async () => {
    const agent = new FakeAcpAgent(2);
    const first = await mount(agent);
    await send(first, agent);
    await act(async () => { await agent.chunk("s-1", "Shared"); });
    const second = await mount(agent, "s-1");
    expect(second.result.current.runtime.thread.getState().isRunning).toBe(true);
    expect(text(second)).toContain("Shared");
    act(() => second.result.current.runtime.thread.cancelRun());
    await waitFor(() => expect(first.result.current.runtime.thread.getState().isRunning).toBe(false));
    await waitFor(() => expect(second.result.current.runtime.thread.getState().isRunning).toBe(false));
    expect(text(first)).toBe(text(second));
  });

  test("an unanswered permission closes when another surface returns the session to running", async () => {
    const agent = new FakeAcpAgent(2);
    const hook = await mount(agent);
    await send(hook, agent);
    void agent.ask("s-1");
    await waitFor(() => expect(hook.result.current.permissions).toHaveLength(1));
    expect(hook.result.current.runtime.thread.getState().isRunning).toBe(true);
    await act(async () => { await agent.state("s-1", "running"); });
    await waitFor(() => expect(hook.result.current.permissions).toHaveLength(0));
    await waitFor(() => expect(agent.permissions).toEqual([{ outcome: { outcome: "cancelled" } }]));
  });

  test("rejects an unsupported endpoint protocol with translated copy", async () => {
    const agent = new FakeAcpAgent(2);
    const hook = await mount(agent, undefined, 7);
    expect(hook.result.current.error).toBe("This agent uses an unsupported chat protocol.");
    expect(agent.initialized).toHaveLength(0);
  });
});

describe("ACP v1 runtime", () => {
  test("Stop cancels its prompt-bound turn", async () => {
    const agent = new FakeAcpAgent(1);
    const hook = await mount(agent);
    await send(hook, agent);
    await act(async () => { await agent.chunk("s-1", "Partial"); });
    act(() => hook.result.current.runtime.thread.cancelRun());
    await waitFor(() => expect(hook.result.current.runtime.thread.getState().isRunning).toBe(false));
    await waitFor(() => expect(hook.result.current.runtime.thread.getState().messages.at(-1)?.status).toEqual({ type: "incomplete", reason: "cancelled" }));
  });

  test("keeps the prompt-bound lifecycle and streams the existing transcript", async () => {
    const agent = new FakeAcpAgent(1);
    const hook = await mount(agent);
    await send(hook, agent);
    await act(async () => { await agent.chunk("s-1", "Container reply"); });
    await waitFor(() => expect(text(hook)).toContain("Container reply"));
    expect(hook.result.current.runtime.thread.getState().isRunning).toBe(true);
    await act(async () => { await agent.finish("s-1"); });
    await waitFor(() => expect(hook.result.current.runtime.thread.getState().isRunning).toBe(false));
    expect(agent.initialized).toEqual([1]);
  });

  test.each(["load", "resume"] as const)("reconnect uses advertised session/%s on the same session", async (method) => {
    const agent = new FakeAcpAgent(1, { list: true, load: method === "load", resume: method === "resume" });
    const hook = await mount(agent);
    await send(hook, agent);
    await act(async () => { await agent.chunk("s-1", "Retained"); await agent.finish("s-1"); });
    act(() => hook.result.current.reconnect());
    await waitFor(() => expect(hook.result.current.status).toBe("ready"));
    expect(agent.sessions.size).toBe(1);
    expect(agent.restored[0]).toMatchObject({ method: `session/${method}`, sessionId: "s-1" });
    await waitFor(() => expect(text(hook)).toContain("Retained"));
  });
});


test.each([1, 2] as const)("v%s mounts without creating and creates lazily on first send", async (version) => {
  const agent = new FakeAcpAgent(version);
  const first = await mount(agent);
  expect(agent.sessions.size).toBe(0);
  first.unmount();
  const second = await mount(agent);
  expect(agent.sessions.size).toBe(0);
  await send(second, agent);
  expect(agent.sessions.size).toBe(1);
});

test("Chat tab reload mid-turn resumes newest and follows the same turn", async () => {
  const agent = new FakeAcpAgent(2);
  const first = await mount(agent);
  await send(first, agent);
  await act(async () => { await agent.chunk("s-1", "Before reload"); });
  first.unmount();
  const second = await mount(agent);
  expect(second.result.current.sessions.currentId).toBe("s-1");
  expect(agent.sessions.size).toBe(1);
  expect(text(second)).toContain("Before reload");
  expect(second.result.current.runtime.thread.getState().isRunning).toBe(true);
  await act(async () => { await agent.chunk("s-1", " after"); await agent.finish("s-1"); });
  await waitFor(() => expect(text(second)).toContain("Before reload after"));
  expect(second.result.current.runtime.thread.getState().isRunning).toBe(false);
});

test("chatter latest-session updates and rail toggles never reconnect the binding", async () => {
  const agent = new FakeAcpAgent(2);
  const options: Partial<AcpRuntimeOptions> = {};
  const hook = await mount(agent, undefined, 2, options);
  await send(hook, agent);
  await act(async () => { await agent.chunk("s-1", "Retained"); });
  options.knownSessionId = "s-1";
  hook.rerender();
  options.knownSessionId = "latest-other-tab";
  options.showSessions = true;
  hook.rerender();
  await waitFor(() => expect(hook.result.current.sessions.items).toHaveLength(1));
  expect(agent.opened).toHaveLength(1);
  expect(agent.restored).toHaveLength(0);
  expect(text(hook)).toContain("Retained");
});

test.each([true, false])("v2 keeps Stop through acceptance in either running/response order (%s)", async (stateBeforeResponse) => {
  const agent = new FakeAcpAgent(2); agent.stateBeforeResponse = stateBeforeResponse;
  const hook = await mount(agent);
  act(() => hook.result.current.runtime.thread.append("Pending"));
  expect(hook.result.current.runtime.thread.getState().isRunning).toBe(true);
  await waitFor(() => expect(agent.promptCalls).toBe(1));
  await waitFor(() => expect(hook.result.current.runtime.thread.getState().messages.filter((message) => message.role === "user")).toHaveLength(1));
  expect(hook.result.current.runtime.thread.getState().isRunning).toBe(true);
  if (!stateBeforeResponse) {
    expect(agent.sessions.get("s-1")?.state).toBe("idle");
    await act(async () => { await agent.state("s-1", "running"); });
  }
  await act(async () => { await agent.chunk("s-1", "Accepted turn"); await agent.finish("s-1"); });
  await waitFor(() => expect(hook.result.current.runtime.thread.getState().isRunning).toBe(false));
});

test.each(["first-running", "second-running", "completed"] as const)("v2 brackets queued turns across tabs and replay (%s)", async (replayAt) => {
  const agent = new FakeAcpAgent(2);
  const first = await mount(agent);
  await send(first, agent, "First");
  await act(async () => { await agent.update("s-1", { sessionUpdate: "agent_message_chunk", messageId: "opaque-first", content: { type: "text", text: "First answer" } }); });
  const second = await mount(agent, "s-1");
  await send(second, agent, "Second");
  await waitFor(() => expect(first.result.current.runtime.thread.getState().messages.filter((message) => message.role === "user")).toHaveLength(2));
  expect(agent.sessions.get("s-1")?.v2History.filter((note) => note.update.sessionUpdate === "state_update" && note.update.state === "running")).toHaveLength(1);
  const replay = async () => {
    const before = agent.restored.length;
    act(() => first.result.current.reconnect());
    await waitFor(() => expect(agent.restored).toHaveLength(before + 1));
    await waitFor(() => expect(first.result.current.status).toBe("ready"));
    expect(text(first)).toBe(text(second));
  };
  if (replayAt === "first-running") await replay();
  await act(async () => {
    await agent.update("s-1", { sessionUpdate: "agent_thought_chunk", messageId: "opaque-thought", content: { type: "text", text: "First thought after queue" } });
    await agent.update("s-1", { sessionUpdate: "tool_call_update", toolCallId: "opaque-tool", title: "First tool", status: "completed" });
    await agent.update("s-1", { sessionUpdate: "agent_message_chunk", messageId: "opaque-after", content: { type: "text", text: "First after queue" } });
  });
  expect(first.result.current.runtime.thread.getState().messages.find((message) => message.id === "assistant-m-1")?.content.map((part) => part.type)).toEqual(["text", "reasoning", "tool-call", "text"]);
  await act(async () => { await agent.finish("s-1", "_angee/failed"); });
  const state = first.result.current.runtime.thread.getState();
  expect(state.isRunning).toBe(true);
  const failed = state.messages.find((message) => message.id === "assistant-m-1");
  expect(failed?.status).toEqual({ type: "incomplete", reason: "error" });
  expect(failed?.content).toContainEqual({ type: "text", text: "Stored readable error" });
  expect(failed?.content).not.toContainEqual({ type: "text", text: "The agent could not complete this turn. You can send another message." });
  expect(state.messages.find((message) => message.id === "assistant-m-2")?.content).toEqual([]);
  if (replayAt === "second-running") await replay();
  await act(async () => { await agent.chunk("s-1", "Second answer"); await agent.finish("s-1"); });
  await waitFor(() => expect(first.result.current.runtime.thread.getState().isRunning).toBe(false));
  if (replayAt === "completed") await replay();
  expect(text(first)).toBe(text(second));
  expect(first.result.current.runtime.thread.getState().messages.find((message) => message.id === "assistant-m-2")?.status).toEqual({ type: "complete", reason: "stop" });
  const transcript = (hook: typeof first) => hook.result.current.runtime.thread.getState().messages.map(({ id, role, content, status }) => ({ id, role, content, status }));
  expect(transcript(first)).toEqual(transcript(second));
});

test.each(["refusal", "max_tokens", "max_turn_requests"] as const)("v2 renders %s as stopped rather than a normal completion", async (reason) => {
  const agent = new FakeAcpAgent(2);
  const hook = await mount(agent);
  await send(hook, agent);
  await act(async () => { await agent.finish("s-1", reason); });
  await waitFor(() => expect(text(hook)).toContain("The agent stopped before completing this turn. You can send another message."));
  expect(hook.result.current.runtime.thread.getState().messages.at(-1)?.status?.type).toBe("incomplete");
});

test("explicit protocol disagreement fails before opening a socket", async () => {
  const agent = new FakeAcpAgent(1);
  const providers = createAcpTestProviders(agent);
  fixtures.push(agent, { close: providers.clearClients });
  transport.open.mockImplementation(agent.open);
  const hook = renderHook(() => useAcpRuntime({ agentId: "agt-1", view, protocolVersion: 2 }), { wrapper: providers.Provider });
  await waitFor(() => expect(hook.result.current.status).toBe("error"));
  expect(hook.result.current.error).toBe("This agent uses an unsupported chat protocol.");
  expect(agent.opened).toHaveLength(0);
});

test.each([1, 2] as const)("v%s removes or marks a rejected optimistic prompt and restores composer text", async (version) => {
  const agent = new FakeAcpAgent(version); agent.failPrompt = true;
  const hook = await mount(agent);
  await send(hook, agent, "Keep this message");
  await waitFor(() => expect(hook.result.current.error).toBe("The agent did not respond."));
  const echo = hook.result.current.runtime.thread.getState().messages.find((message) => message.role === "user");
  expect(echo?.metadata.custom.acpDeliveryFailed).toBe(true);
  expect(hook.result.current.runtime.thread.composer.getState().text).toBe("Keep this message");
  expect(hook.result.current.runtime.thread.getState().isRunning).toBe(false);
});

test("a send racing reconnect is retained and visibly reported instead of dropped", async () => {
  const agent = new FakeAcpAgent(2);
  let release: (value: string) => void = () => undefined;
  const context = new Promise<string>((resolve) => { release = resolve; });
  const hook = await mount(agent, undefined, 2, {}, { renderPrompt: () => context });
  act(() => hook.result.current.runtime.thread.append("Retain this"));
  await waitFor(() => expect(agent.sessions.size).toBe(1));
  act(() => agent.opened[0]?.close());
  await act(async () => { release(""); });
  await waitFor(() => expect(hook.result.current.error).toBe("Your message was not sent. Try again when the agent is connected."));
  expect(agent.promptCalls).toBe(0);
  expect(hook.result.current.runtime.thread.composer.getState().text).toBe("Retain this");
});

test.each(["load", "resume", "none"] as const)("v1 refresh stays ready and keeps its transcript with %s restoration", async (restoration) => {
  const agent = new FakeAcpAgent(1, { list: true, load: restoration === "load", resume: restoration === "resume" });
  let minted = 0;
  let release: () => void = () => undefined;
  const refreshed = new Promise<void>((resolve) => { release = resolve; });
  const hook = await mount(agent, undefined, 1, {}, {
    mint: async () => { if (++minted === 2) await refreshed; },
    expiresAt: () => minted === 1 ? new Date(Date.now() + 60_150).toISOString() : "",
  });
  await send(hook, agent);
  await act(async () => { await agent.chunk("s-1", "Keep transcript"); await agent.finish("s-1"); });
  await waitFor(() => expect(minted).toBe(2));
  expect(hook.result.current.status).toBe("ready");
  expect(text(hook)).toContain("Keep transcript");
  await act(async () => { release(); });
  await waitFor(() => expect(agent.opened).toHaveLength(2));
  expect(hook.result.current.status).toBe("ready");
  expect(text(hook)).toContain("Keep transcript");
  expect(agent.sessions.size).toBe(1);
});

test("refresh timers never exceed the platform timeout maximum", async () => {
  const timer = vi.spyOn(globalThis, "setTimeout");
  await mount(new FakeAcpAgent(1), undefined, 1, {}, { expiresAt: () => new Date(Date.now() + 3_000_000_000).toISOString() });
  expect(timer.mock.calls.some((call) => call[1] === 2_147_483_647)).toBe(true);
  expect(timer.mock.calls.every((call) => Number(call[1] ?? 0) <= 2_147_483_647)).toBe(true);
});

test("transient endpoint failures retry automatically", async () => {
  const agent = new FakeAcpAgent(2);
  let attempts = 0;
  const hook = await mount(agent, undefined, 2, {}, { mint: async () => { if (++attempts === 1) throw new Error("Temporary mint failure"); } });
  expect(attempts).toBe(2);
  expect(hook.result.current.status).toBe("ready");
  expect(agent.sessions.size).toBe(0);
});


test("transient open failures back off exponentially to a cap and reset after ready", async () => {
  vi.useFakeTimers();
  const agent = new FakeAcpAgent(2);
  const providers = createAcpTestProviders(agent);
  fixtures.push(agent, { close: providers.clearClients });
  let attempts = 0;
  transport.open.mockImplementation((...args: Parameters<typeof agent.open>) => {
    if (++attempts <= 7) throw new Error("Temporary open failure");
    return agent.open(...args);
  });
  const hook = renderHook(() => useAcpRuntime({ agentId: "agt-1", view }), { wrapper: providers.Provider });
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(attempts).toBe(1);
  for (const delay of [1000, 2000, 4000, 8000, 16000, 30000, 30000]) {
    const before = attempts;
    await act(async () => { await vi.advanceTimersByTimeAsync(delay - 1); });
    expect(attempts).toBe(before);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(attempts).toBe(before + 1);
  }
  expect(hook.result.current.status).toBe("ready");
  await act(async () => { agent.opened.at(-1)?.close(); await vi.advanceTimersByTimeAsync(0); });
  await act(async () => { await vi.advanceTimersByTimeAsync(999); });
  expect(attempts).toBe(8);
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(attempts).toBe(9);
  expect(hook.result.current.status).toBe("ready");
});


test("a message submitted after disconnect stays visible as failed without overwriting a new draft", async () => {
  const agent = new FakeAcpAgent(2);
  const hook = await mount(agent);
  await send(hook, agent, "Accepted");
  await act(async () => { agent.opened[0]?.close(); });
  await waitFor(() => expect(hook.result.current.status).toBe("closed"));
  act(() => {
    hook.result.current.runtime.thread.composer.setText("New draft");
    hook.result.current.runtime.thread.append("Unsent during reconnect");
  });
  await waitFor(() => expect(text(hook)).toContain("Unsent during reconnect"));
  expect(hook.result.current.runtime.thread.getState().messages.find((message) => message.role === "user" && message.content.some((part) => part.type === "text" && part.text === "Unsent during reconnect"))?.metadata.custom.acpDeliveryFailed).toBe(true);
  expect(hook.result.current.runtime.thread.composer.getState().text).toBe("New draft");
  expect(agent.promptCalls).toBe(1);
  act(() => hook.result.current.reconnect());
  await waitFor(() => expect(hook.result.current.status).toBe("ready"));
  expect(text(hook)).toContain("Unsent during reconnect");
  expect(hook.result.current.runtime.thread.composer.getState().text).toBe("New draft");
});


test("v1 resume-only restores the newest live session without claiming replayable history", async () => {
  const agent = new FakeAcpAgent(1, { list: true, load: false, resume: true });
  await agent.history("Previous conversation", "Previous answer");
  const hook = await mount(agent);
  expect(agent.sessions.size).toBe(1);
  expect(agent.restored).toEqual([{ method: "session/resume", sessionId: "s-1" }]);
  expect(hook.result.current.sessions.available).toBe(false);
  expect(hook.result.current.sessions.currentId).toBe("s-1");
  expect(text(hook)).toBe("");
  await send(hook, agent, "Continue this session");
  await act(async () => { await agent.chunk("s-1", "Local answer"); await agent.finish("s-1"); });
  act(() => hook.result.current.reconnect());
  await waitFor(() => expect(agent.restored).toHaveLength(2));
  await waitFor(() => expect(hook.result.current.status).toBe("ready"));
  expect(text(hook)).toContain("Local answer");
  expect(text(hook)).not.toContain("Previous answer");
});
