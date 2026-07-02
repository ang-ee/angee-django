// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { AgentRosterItem, AgentSession } from "../documents";

const sdkMocks = vi.hoisted(() => ({
  calls: [] as Array<{
    operation: string;
    options: unknown;
    variables: unknown;
  }>,
  rosterData: { agents: [] as unknown[] },
  rosterFetching: false,
  sessionData: undefined as unknown,
  sessionFetching: false,
  useAuthoredQuery: vi.fn(),
}));

vi.mock("@tanstack/react-router", async () => {
  const ReactModule = await import("react");
  return {
    Link: ReactModule.forwardRef<
      HTMLAnchorElement,
      { to?: unknown; children?: React.ReactNode }
    >(function Link({ to, children, ...rest }, ref) {
      return ReactModule.createElement(
        "a",
        { ref, href: typeof to === "string" ? to : String(to ?? ""), ...rest },
        children,
      );
    }),
  };
});

vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  return {
    ...actual,
    useNamespaceT:
      (_namespace: string, messages: Record<string, string>) =>
      (key: string) =>
        messages[key] ?? key,
  };
});

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: sdkMocks.useAuthoredQuery,
}));

vi.mock("./AgentChat", () => ({
  AgentChat: ({
    agentId,
    fallbackName,
    modelHandle,
    selectedAgentId,
    view,
  }: {
    agentId: string;
    fallbackName?: string;
    modelHandle?: string;
    selectedAgentId?: string;
    view?: unknown;
  }) => (
    <section
      data-testid="agent-chat"
      data-agent-id={agentId}
      data-model-handle={modelHandle}
      data-selected-agent-id={selectedAgentId}
      data-view={JSON.stringify(view)}
    >
      {fallbackName}
    </section>
  ),
}));

import { AgentChatterPane } from "./AgentChatterPane";

function operationName(document: unknown): string {
  const definitions = (document as { definitions?: Array<{ name?: { value?: string } }> })
    .definitions;
  return definitions?.[0]?.name?.value ?? "";
}

function agent(
  id: string,
  name: string,
  overrides: Partial<AgentRosterItem> = {},
): AgentRosterItem {
  return {
    id,
    name,
    runtime_status: "RUNNING",
    is_template: false,
    updated_at: "2026-06-27T00:00:00Z",
    model: { name: "claude-sonnet-4-6" },
    ...overrides,
  } as AgentRosterItem;
}

function session(overrides: Partial<AgentSession> = {}): AgentSession {
  return {
    agent_id: "agt_running",
    agent_name: "Demo Agent",
    status: "running",
    model_handle: "claude-opus-4-8",
    ...overrides,
  } as AgentSession;
}

beforeEach(() => {
  sdkMocks.calls = [];
  sdkMocks.rosterData = { agents: [] };
  sdkMocks.rosterFetching = false;
  sdkMocks.sessionData = undefined;
  sdkMocks.sessionFetching = false;
  sdkMocks.useAuthoredQuery.mockReset();
  sdkMocks.useAuthoredQuery.mockImplementation(
    (document: unknown, variables: unknown, options: unknown) => {
      const operation = operationName(document);
      sdkMocks.calls.push({ operation, variables, options });
      if (operation === "AgentRoster") {
        return {
          data: sdkMocks.rosterData,
          fetching: sdkMocks.rosterFetching,
          error: null,
          refetch: vi.fn(),
        };
      }
      if (operation === "ResolveSessionForView") {
        return {
          data: sdkMocks.sessionData,
          fetching: sdkMocks.sessionFetching,
          error: null,
          refetch: vi.fn(),
        };
      }
      throw new Error(`Unexpected authored query: ${operation}`);
    },
  );
});

afterEach(cleanup);

describe("AgentChatterPane", () => {
  test("leaves the no-agent state when the session query refetches with a running agent", async () => {
    sdkMocks.sessionData = { resolve_session_for_view: null };

    const view = render(<AgentChatterPane resource="notes/note" recordId="nte_1" />);

    expect(screen.getByText("No agent yet")).toBeTruthy();

    sdkMocks.rosterData = {
      agents: [agent("agt_running", "Demo Agent")],
    };
    sdkMocks.sessionData = {
      resolve_session_for_view: session(),
    };
    view.rerender(<AgentChatterPane resource="notes/note" recordId="nte_1" />);

    expect(screen.queryByText("No agent yet")).toBeNull();
    // The chat surface is lazy-mounted (its own chunk), so it resolves a tick after render.
    const chat = await screen.findByTestId("agent-chat");
    expect(chat.getAttribute("data-agent-id")).toBe("agt_running");
    expect(chat.getAttribute("data-selected-agent-id")).toBe("agt_running");
    expect(chat.getAttribute("data-model-handle")).toBe("claude-sonnet-4-6");
    expect(chat.getAttribute("data-view")).toBe(
      JSON.stringify({ kind: "record", type: "notes/note", sqid: "nte_1" }),
    );
    expect(chat.textContent).toBe("Demo Agent");
    expect(
      sdkMocks.calls.some(
        (call) =>
          call.operation === "ResolveSessionForView" &&
          JSON.stringify(call.options) === JSON.stringify({ models: ["agents.Agent"] }),
      ),
    ).toBe(true);
  });

  test("uses a contributed chatter view directly", async () => {
    sdkMocks.rosterData = {
      agents: [agent("agt_running", "Demo Agent")],
    };
    sdkMocks.sessionData = {
      resolve_session_for_view: session(),
    };

    render(
      <AgentChatterPane
        view={{
          kind: "record",
          type: "storage/file",
          sqid: "fil_1",
          params: { id: "fil_1" },
        }}
      />,
    );

    const chat = await screen.findByTestId("agent-chat");
    expect(chat.getAttribute("data-view")).toBe(
      JSON.stringify({
        kind: "record",
        type: "storage/file",
        sqid: "fil_1",
        params: { id: "fil_1" },
      }),
    );
    expect(
      sdkMocks.calls.some(
        (call) =>
          call.operation === "ResolveSessionForView" &&
          JSON.stringify(call.variables) ===
            JSON.stringify({ view: { kind: "dashboard", type: "storage/file" } }),
      ),
    ).toBe(true);
  });
});
