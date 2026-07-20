// @vitest-environment happy-dom

import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const hookMocks = vi.hoisted(() => ({
  externalConfigs: [] as Array<Record<string, unknown>>,
  latestData: undefined as unknown,
  turnsData: undefined as unknown,
  queryCalls: [] as Array<{ operation: string; variables: Record<string, string> }>,
  latestRefetch: vi.fn(),
  turnsRefetch: vi.fn(),
  startSession: vi.fn(),
  postMessage: vi.fn(),
  renderPrompt: vi.fn(),
  useAuthoredMutation: vi.fn(),
  useAuthoredQuery: vi.fn(),
  useExternalStoreRuntime: vi.fn(),
}));

vi.mock("@assistant-ui/react", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@assistant-ui/react")>()),
  useExternalStoreRuntime: hookMocks.useExternalStoreRuntime,
}));

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredMutation: hookMocks.useAuthoredMutation,
  useAuthoredQuery: hookMocks.useAuthoredQuery,
}));

import { useSessionRuntime } from "./useSessionRuntime";

const VIEW = { kind: "record", type: "notes/note", sqid: "nte_1" } as const;

beforeEach(() => {
  hookMocks.externalConfigs = [];
  hookMocks.latestData = undefined;
  hookMocks.turnsData = undefined;
  hookMocks.queryCalls = [];
  hookMocks.latestRefetch.mockReset();
  hookMocks.turnsRefetch.mockReset();
  hookMocks.startSession.mockReset();
  hookMocks.postMessage.mockReset().mockResolvedValue({ post_agent_message: { id: "atn_new" } });
  hookMocks.renderPrompt.mockReset().mockResolvedValue({ render_agent_prompt: "context" });
  hookMocks.useAuthoredMutation.mockReset();
  hookMocks.useAuthoredQuery.mockReset();
  hookMocks.useExternalStoreRuntime.mockReset();
  hookMocks.useExternalStoreRuntime.mockImplementation((config: Record<string, unknown>) => {
    hookMocks.externalConfigs.push(config);
    return { kind: "external-runtime" };
  });
  hookMocks.useAuthoredMutation.mockImplementation((document: unknown) => {
    switch (operationName(document)) {
      case "StartAgentSession":
        return [hookMocks.startSession, { fetching: false, error: null }];
      case "PostAgentMessage":
        return [hookMocks.postMessage, { fetching: false, error: null }];
      case "RenderAgentPrompt":
        return [hookMocks.renderPrompt, { fetching: false, error: null }];
      default:
        throw new Error(`Unexpected mutation ${operationName(document)}`);
    }
  });
  hookMocks.useAuthoredQuery.mockImplementation(
    (document: unknown, variables: Record<string, string>) => {
      const operation = operationName(document);
      hookMocks.queryCalls.push({ operation, variables });
      if (operation === "LatestAgentSession") {
        return {
          data: hookMocks.latestData,
          fetching: false,
          error: null,
          refetch: hookMocks.latestRefetch,
        };
      }
      if (operation === "AgentSessionTurns") {
        return {
          data: hookMocks.turnsData,
          fetching: false,
          error: null,
          refetch: hookMocks.turnsRefetch,
        };
      }
      throw new Error(`Unexpected query ${operation}`);
    },
  );
});

afterEach(cleanup);

describe("useSessionRuntime", () => {
  test("keeps completed-turn message identities stable across refetch and only refolds the streaming turn", () => {
    hookMocks.latestData = { agent_sessions: [{ id: "ase_1", status: "IDLE" }] };
    hookMocks.turnsData = sessionTurns([
      turn("atn_1", "First", [textUpdate("First answer")]),
      turn("atn_2", "Second", [textUpdate("Second")]),
    ]);
    const rendered = renderHook(() => useSessionRuntime("agt_1", VIEW));
    const firstMessages = latestMessages();

    hookMocks.latestData = { agent_sessions: [{ id: "ase_1", status: "IDLE" }] };
    hookMocks.turnsData = sessionTurns([
      turn("atn_1", "First", [textUpdate("First answer")]),
      turn("atn_2", "Second", [textUpdate("Second")]),
    ]);
    rendered.rerender();
    const refetchedMessages = latestMessages();

    expect(refetchedMessages).toEqual(firstMessages);
    expect(refetchedMessages.every((message, index) => message === firstMessages[index])).toBe(true);

    hookMocks.turnsData = sessionTurns([
      turn("atn_1", "First", [textUpdate("First answer")]),
      turn("atn_2", "Second", [textUpdate("Second"), textUpdate(" answer")]),
    ]);
    rendered.rerender();
    const streamedMessages = latestMessages();

    expect(streamedMessages[0]).toBe(firstMessages[0]);
    expect(streamedMessages[1]).toBe(firstMessages[1]);
    expect(streamedMessages[2]).not.toBe(firstMessages[2]);
    expect(streamedMessages[3]).not.toBe(firstMessages[3]);
    expect(streamedMessages[3]).toMatchObject({ id: "assistant-atn_2" });

    act(() => {
      const runtime = rendered.result.current;
      runtime.reconnect();
    });
    expect(hookMocks.latestRefetch).toHaveBeenCalledTimes(1);
    expect(hookMocks.turnsRefetch).toHaveBeenCalledTimes(1);
  });

  test("ignores a stale start-session continuation after an in-place agent switch", async () => {
    hookMocks.latestData = { agent_sessions: [] };
    const pending = new Map<string, ReturnType<typeof deferredSession>>();
    hookMocks.startSession.mockImplementation(({ agent }: { agent: string }) => {
      const deferred = deferredSession();
      pending.set(agent, deferred);
      return deferred.promise;
    });
    const rendered = renderHook(
      ({ agentId }: { agentId: string }) => useSessionRuntime(agentId, VIEW),
      { initialProps: { agentId: "agt_a" } },
    );
    await waitFor(() => expect(pending.has("agt_a")).toBe(true));

    rendered.rerender({ agentId: "agt_b" });
    await waitFor(() => expect(pending.has("agt_b")).toBe(true));

    await act(async () => {
      pending.get("agt_a")?.resolve({ start_agent_session: { id: "ase_a" } });
      await Promise.resolve();
    });
    expect(
      hookMocks.queryCalls.some(
        (call) => call.operation === "AgentSessionTurns" && call.variables.sessionId === "ase_a",
      ),
    ).toBe(false);

    hookMocks.turnsData = sessionTurns([] as ReturnType<typeof turn>[] , "ase_b");
    await act(async () => {
      pending.get("agt_b")?.resolve({ start_agent_session: { id: "ase_b" } });
      await Promise.resolve();
    });
    await waitFor(() =>
      expect(
        hookMocks.queryCalls.some(
          (call) => call.operation === "AgentSessionTurns" && call.variables.sessionId === "ase_b",
        ),
      ).toBe(true),
    );
  });
});

function operationName(document: unknown): string {
  return (
    (document as { definitions?: Array<{ name?: { value?: string } }> })
      .definitions?.[0]?.name?.value ?? ""
  );
}

function textUpdate(text: string): Record<string, unknown> {
  return { sessionUpdate: "agent_message_chunk", content: { type: "text", text } };
}

function turn(id: string, prompt: string, updates: Record<string, unknown>[]) {
  return { id, prompt, updates };
}

function sessionTurns(turns: ReturnType<typeof turn>[], sessionId = "ase_1") {
  return {
    agent_sessions: [
      {
        id: sessionId,
        status: "IDLE",
        last_error: "",
        agent: { model: { name: "test-model" } },
      },
    ],
    agent_turns: turns,
  };
}

function latestMessages(): Array<Record<string, unknown>> {
  const config = hookMocks.externalConfigs.at(-1);
  return (config?.messages ?? []) as Array<Record<string, unknown>>;
}

function deferredSession() {
  let resolve!: (value: { start_agent_session: { id: string } }) => void;
  const promise = new Promise<{ start_agent_session: { id: string } }>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
