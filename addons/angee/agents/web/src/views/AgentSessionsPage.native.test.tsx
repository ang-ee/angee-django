// @vitest-environment happy-dom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { createRootRoute, createRoute, createRouter, createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { PrimaryPaneTestHost, ShellPageTestProviders } from "@angee/app/testing";
import { createRouteHref } from "@angee/ui/runtime";

import { FakeAcpAgent } from "../acp-test-agent";
import { createAcpTestProviders } from "../acp-test-providers";
import agents from "../index";
import { AgentSessionsPage } from "./AgentSessionsPage";

const transport = vi.hoisted(() => ({ open: vi.fn() }));
vi.mock("../acp-transport", async (original) => ({
  ...await original<typeof import("../acp-transport")>(), openAcpTransport: transport.open,
}));
vi.mock("@angee/ui", async (original) => ({
  ...await original<typeof import("@angee/ui")>(), useRouteRecordId: () => "agt-1", useRouteHref: () => href,
}));
const href = createRouteHref(agents.routes ?? []);
const disposals: Array<() => void> = [];
afterEach(() => { cleanup(); disposals.splice(0).forEach((dispose) => dispose()); });

function mount(agent: FakeAcpAgent, location = "/agents/sessions/agt-1?keep=scope") {
  const providers = createAcpTestProviders(agent);
  disposals.push(() => agent.close(), providers.clearClients);
  transport.open.mockImplementation(agent.open);
  const root = createRootRoute();
  const route = createRoute({ getParentRoute: () => root, path: "/agents/sessions/$id",
    validateSearch: (search: Record<string, unknown>) => search,
    component: () => <><AgentSessionsPage /><PrimaryPaneTestHost /></>,
  });
  const router = createRouter({ routeTree: root.addChildren([route]), history: createMemoryHistory({ initialEntries: [location] }) });
  const mounted = render(<providers.Provider><ShellPageTestProviders><RouterProvider router={router} /></ShellPageTestProviders></providers.Provider>);
  return { ...mounted, router, providers };
}

describe.each([1, 2] as const)("ACP v%s sessions page", (version) => {
  test("resumes newest without inserting an empty session, paginates, switches by URL and creates", async () => {
    const agent = new FakeAcpAgent(version);
    const first = await agent.history("First conversation", "Earlier answer");
    await agent.history("Second conversation", "Middle answer");
    const newest = await agent.history("Third conversation", "Latest answer");
    const mounted = mount(agent);
    await screen.findByText("Latest answer");
    const rail = screen.getByRole("navigation", { name: "Sessions" });
    expect(agent.sessions.size).toBe(3);
    expect(agent.listCalls).toBe(1);
    await waitFor(() => expect(mounted.router.state.location.search).toEqual({ keep: "scope", session: newest }));
    expect(within(rail).queryByRole("button", { name: /First conversation/ })).toBeNull();
    fireEvent.click(within(rail).getByRole("button", { name: "Load more sessions" }));
    fireEvent.click(await within(rail).findByRole("button", { name: /First conversation/ }));
    await screen.findByText("Earlier answer");
    expect(mounted.router.state.location.search).toEqual({ keep: "scope", session: first });
    expect(agent.restored.at(-1)?.sessionId).toBe(first);
    expect(within(rail).getByRole("button", { name: /First conversation/ }).getAttribute("aria-current")).toBe("page");
    fireEvent.click(screen.getByRole("button", { name: "New session" }));
    await waitFor(() => expect(agent.sessions.size).toBe(4));
    await waitFor(() => expect(mounted.router.state.location.search).toEqual({ keep: "scope", session: "s-4" }));
    await waitFor(() => expect(screen.queryByText("Earlier answer")).toBeNull());
    const cached = mounted.providers.clients[0]?.getQueryCache().getAll().find((query) => query.queryKey.includes("acp"));
    expect(cached?.state.data).toMatchObject({ pages: expect.any(Array), pageParams: expect.any(Array) });
  });
});

test("an empty sessions page inserts nothing until New session or the first send", async () => {
  const agent = new FakeAcpAgent(2);
  const mounted = mount(agent);
  await screen.findByPlaceholderText("Message the agent…");
  expect(agent.sessions.size).toBe(0);
  fireEvent.click(screen.getByRole("button", { name: "New session" }));
  await waitFor(() => expect(mounted.router.state.location.search.session).toBe("s-1"));
  expect(agent.sessions.size).toBe(1);
});

test("reload mid-turn restores the URL session and follows its live output", async () => {
  const agent = new FakeAcpAgent(2);
  const first = mount(agent);
  const input = await screen.findByPlaceholderText("Message the agent…");
  fireEvent.change(input, { target: { value: "Continue" } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(agent.promptCalls).toBe(1));
  await act(async () => { await agent.chunk("s-1", "Before reload"); });
  await screen.findByText("Before reload");
  await waitFor(() => expect(first.router.state.location.search.session).toBe("s-1"));
  const location = first.router.state.location.href;
  first.unmount();
  const second = mount(agent, location);
  await screen.findByText("Before reload");
  expect(screen.getByRole("button", { name: "Stop" })).toBeTruthy();
  expect(agent.sessions.size).toBe(1);
  await act(async () => { await agent.chunk("s-1", " and after"); await agent.finish("s-1"); });
  await screen.findByText("Before reload and after");
  expect(second.router.state.location.search.session).toBe("s-1");
});

test("a repeated cursor ends native pagination", async () => {
  const agent = new FakeAcpAgent(2); agent.repeatCursor = true;
  for (const title of ["First", "Second", "Third", "Fourth", "Fifth"]) await agent.history(title, `${title} answer`);
  mount(agent);
  const more = await screen.findByRole("button", { name: "Load more sessions" });
  fireEvent.click(more);
  await screen.findByRole("button", { name: /Second/ });
  await waitFor(() => expect(screen.queryByRole("button", { name: "Load more sessions" })).toBeNull());
  expect(agent.listCalls).toBe(2);
});

test.each([{ list: false, load: true, resume: false }, { list: true, load: false, resume: true }])("offers New session without history capabilities (%j)", async (capabilities) => {
  const agent = new FakeAcpAgent(1, capabilities);
  await agent.history("Hidden history", "Hidden answer");
  mount(agent);
  const input = await screen.findByPlaceholderText("Message the agent…");
  expect(screen.getByRole("button", { name: "New session" })).toBeTruthy();
  expect(screen.queryByText("Hidden history")).toBeNull();
  expect(agent.listCalls).toBe(capabilities.resume ? 1 : 0);
  expect(agent.sessions.size).toBe(1);
  fireEvent.change(input, { target: { value: "Live prompt" } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(agent.promptCalls).toBe(1));
  await screen.findByRole("button", { name: "Live session" });
});
