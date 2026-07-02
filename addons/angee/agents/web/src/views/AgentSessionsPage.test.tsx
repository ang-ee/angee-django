// @vitest-environment happy-dom

import * as React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import {
  PrimaryPaneTestHost,
  ShellPageTestProviders,
} from "@angee/app/testing";

import type { AgentRosterItem } from "../documents";

const routerMocks = vi.hoisted(() => ({
  navigate: vi.fn(), params: {} as Record<string, string>, }));

const sdkMocks = vi.hoisted(() => ({
  useAuthoredQuery: vi.fn(), }));

vi.mock("@tanstack/react-router", async () => {
  const React = await import("react");
  return {
    useNavigate: () => routerMocks.navigate, useParams: () => routerMocks.params, // A capturing anchor: keeps the `to` href and spreads the merged props (className, // aria-current, data-active) that `SessionRailItem`'s `useRender` injects.
    Link: React.forwardRef<HTMLAnchorElement, { to?: unknown; children?: React.ReactNode }>(
      function Link({ to, children, ...rest }, ref) {
        return React.createElement(
          "a", { ref, href: typeof to === "string" ? to : String(to ?? ""), ...rest }, children, );
      }, ), };
});

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: sdkMocks.useAuthoredQuery,
}));

// `@angee/ui` carries the real rail/empty/skeleton primitives + `recordPath`; only the
// route-id helper and namespace translator are overridden.
vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  const React = await import("react");
  return {
    ...actual,
    useRouteRecordId: () => routerMocks.params.id,
    // Mirror the real `useNamespaceT` contract: a STABLE translator identity (memoized
    // on its inputs). AgentSessionsPage publishes a `t`-derived node into the shell
    // primary pane via `usePrimaryPane`, so an unstable `t` would churn that node and
    // republish on every render — an infinite publish/re-render loop.
    useNamespaceT: (_namespace: string, messages: Record<string, string>) =>
      React.useCallback((key: string) => messages[key] ?? key, [messages]), };
});

// Stub the chat surface so the test never pulls in the assistant-ui runtime — it only
// needs to prove which agent each kept-alive instance is bound to.
vi.mock("./AgentChat", () => ({
  AgentChat: ({ agentId }: { agentId: string }) => (
    <div data-testid="agent-chat" data-agent-id={agentId} />
  ), }));

import { AgentSessionsPage } from "./AgentSessionsPage";

// A fresh element each call: React bails out of re-rendering a referentially-identical
// element, so `rerender` must get a NEW tree to pick up the changed router params.
function harness() {
  return (
    <ShellPageTestProviders>
      <AgentSessionsPage />
      <PrimaryPaneTestHost />
    </ShellPageTestProviders>
  );
}

function renderPage() {
  return render(harness());
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
    updated_at: "2026-06-26T00:00:00Z",
    model: { name: "claude-opus" },
    ...overrides,
  } as AgentRosterItem;
}

function queryResult(data: unknown, fetching = false) {
  return { data, fetching, error: null, refetch: vi.fn() };
}

beforeEach(() => {
  routerMocks.params = {};
  routerMocks.navigate.mockReset();
  sdkMocks.useAuthoredQuery.mockReset();
});

afterEach(() => {
  cleanup();
});

describe("AgentSessionsPage", () => {
  test("loading renders skeleton rail rows and no chat", () => {
    sdkMocks.useAuthoredQuery.mockReturnValue(queryResult(undefined, true));

    renderPage();

    expect(screen.getByRole("navigation", { name: "Running agents" })).toBeTruthy();
    expect(screen.getAllByRole("listitem")).toHaveLength(4);
    expect(screen.queryByTestId("agent-chat")).toBeNull();
  });

  test("no running agent renders the provision empty state linking to /agents", () => {
    sdkMocks.useAuthoredQuery.mockReturnValue(
      queryResult({
        agents: [
          agent("t1", "Template", { is_template: true }),
          agent("s1", "Stopped", { runtime_status: "STOPPED" }),
        ],
      }),
    );

    renderPage();

    expect(screen.getByText("No agent yet")).toBeTruthy();
    const cta = screen.getByRole("link", { name: "Set up your assistant" });
    expect(cta.getAttribute("href")).toBe("/agents");
    expect(screen.queryByTestId("agent-chat")).toBeNull();
  });

  test("filters out templates + non-running agents, one rail row each", () => {
    sdkMocks.useAuthoredQuery.mockReturnValue(
      queryResult({
        agents: [
          agent("a1", "Scout"),
          agent("t1", "Template", { is_template: true }),
          agent("s1", "Stopped", { runtime_status: "STOPPED" }),
        ],
      }),
    );
    routerMocks.params = { id: "a1" };

    renderPage();

    expect(screen.getByText("Scout")).toBeTruthy();
    expect(screen.queryByText("Template")).toBeNull();
    expect(screen.queryByText("Stopped")).toBeNull();
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
  });

  test("the running list shows name + handle + a labelled dot, marks the active row, and offers + New", () => {
    sdkMocks.useAuthoredQuery.mockReturnValue(
      queryResult({
        agents: [
          agent("a1", "Scout", { model: { name: "claude-opus" } }),
          agent("a2", "Ranger", { model: { name: "claude-haiku" } }),
        ],
      }),
    );
    routerMocks.params = { id: "a2" };

    renderPage();

    expect(screen.getByText("Scout")).toBeTruthy();
    expect(screen.getByText("claude-haiku")).toBeTruthy();
    // Every runtime-status dot carries a text alternative (never color-only).
    expect(screen.getAllByRole("img", { name: "Running" })).toHaveLength(2);
    // The active row is the URL's :id (aria-current=page), not aria-selected.
    expect(screen.getByRole("link", { current: "page" }).getAttribute("href")).toBe(
      "/agents/sessions/a2",
    );
    expect(screen.getByRole("link", { name: "New agent" }).getAttribute("href")).toBe(
      "/agents",
    );
  });

  test("no :id with agents redirects to the first running agent", () => {
    sdkMocks.useAuthoredQuery.mockReturnValue(
      queryResult({ agents: [agent("a1", "Scout"), agent("a2", "Ranger")] }),
    );

    renderPage();

    expect(routerMocks.navigate).toHaveBeenCalledWith({
      to: "/agents/sessions/a1",
      replace: true,
    });
  });

  test("a stopped/invalid :id falls through to the first running agent", () => {
    sdkMocks.useAuthoredQuery.mockReturnValue(
      queryResult({ agents: [agent("a1", "Scout"), agent("a2", "Ranger")] }),
    );
    routerMocks.params = { id: "gone" };

    renderPage();

    expect(routerMocks.navigate).toHaveBeenCalledWith({
      to: "/agents/sessions/a1",
      replace: true,
    });
    // An absent id never mounts a chat that would error on mintEndpoint.
    expect(screen.queryByTestId("agent-chat")).toBeNull();
  });

  test("switching :id keeps both agents' chats mounted (keep-alive), hiding the inactive one", () => {
    sdkMocks.useAuthoredQuery.mockReturnValue(
      queryResult({ agents: [agent("a1", "Scout"), agent("a2", "Ranger")] }),
    );
    routerMocks.params = { id: "a1" };

    const view = renderPage();
    expect(screen.getByTestId("agent-chat").getAttribute("data-agent-id")).toBe("a1");

    routerMocks.params = { id: "a2" };
    view.rerender(harness());

    const chats = screen.getAllByTestId("agent-chat");
    expect(chats.map((c) => c.getAttribute("data-agent-id")).sort()).toEqual(["a1", "a2"]);
    const a1 = chats.find((c) => c.getAttribute("data-agent-id") === "a1");
    const a2 = chats.find((c) => c.getAttribute("data-agent-id") === "a2");
    // The previous agent stays mounted but hidden (its transcript + socket preserved);
    // the selected one is shown.
    expect(a1?.parentElement?.hasAttribute("hidden")).toBe(true);
    expect(a2?.parentElement?.hasAttribute("hidden")).toBe(false);
  });
});
