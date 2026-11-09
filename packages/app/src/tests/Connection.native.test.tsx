// @vitest-environment happy-dom
import { act, cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { ServerUnavailableError, useAuthoredMutation, useAuthoredQuery, type TypedDocumentNode } from "@angee/refine";
import { useAppRuntime } from "@angee/ui/runtime";
import { parse } from "graphql";
import { afterEach, expect, test, vi } from "vitest";
import type { Root } from "react-dom/client";
import { createApp, PassthroughChrome } from "../create-app";
import { SERVER_CONNECTION_QUERY_KEY } from "../providers/connection";

const ROWS = parse("query ConnectionRows { rows { id } }") as TypedDocumentNode<{ rows: { id: string }[] }>;
const SAVE = parse("mutation ConnectionSave { save { ok } }") as TypedDocumentNode<{ save: { ok: boolean } }>;
const mounts: { root: Root; host: HTMLElement }[] = [];
afterEach(() => {
  mounts.splice(0).forEach(({ root, host }) => { act(() => root.unmount()); host.remove(); });
  cleanup();
  vi.useRealTimers();
});

/** The HTTP status a request fails with, by its body (`csrf` for the bootstrap); none answers it. */
type Failure = (request: string) => number | undefined;
const healthy: Failure = () => undefined;

async function fixture(initial: Failure = healthy, { requireAuth = true } = {}) {
  let failure = initial;
  let client: QueryClient | undefined;
  const requests: string[] = [];
  const fetch: typeof globalThis.fetch = async (_input, init) => {
    const request = init?.body ? String(init.body) : "csrf";
    requests.push(request);
    const status = failure(request);
    if (status) return new Response("Unavailable", { status });
    return Response.json(request === "csrf" ? { token: "fixture", cookieName: null } : { data: {
      __typename: "Query",
      current_user: { id: "person-1", username: "fixture", firstName: "", lastName: "", email: "", isStaff: false, isActive: true, preferences: {}, roleRefs: [] },
      rows: [{ id: "row-1" }],
      save: { ok: true },
    } });
  };
  function Page() {
    client = useQueryClient();
    const read = useAuthoredQuery(ROWS, {});
    const [save] = useAuthoredMutation(SAVE);
    const session = useAppRuntime().auth?.status;
    return <>
      <h1>Requested page</h1>
      <output aria-label="Session">{session}</output>
      <input aria-label="Draft" defaultValue="Unsaved changes" />
      <button onClick={() => { void read.refetch(); }}>Refresh rows</button>
      <button onClick={() => { void save().catch(() => undefined); }}>Save draft</button>
      <span>{read.data?.rows[0]?.id}</span>
    </>;
  }
  history.replaceState(null, "", "/connection-test");
  const host = document.createElement("div");
  document.body.append(host);
  const app = createApp({
    addons: [{ id: "connection-test", routes: [{ name: "connection-test", path: "/connection-test", layout: "private", component: Page }] }],
    layouts: { private: { chrome: PassthroughChrome, requireAuth, schema: "public" } },
    schemas: { public: { url: "/graphql/public/", fetch } },
  });
  let root!: Root;
  await act(async () => { root = app.mount(host); });
  mounts.push({ root, host });
  return {
    requests,
    count: (operation: string) => requests.filter(request => request.includes(operation)).length,
    fail: (next: Failure) => { failure = next; },
    client: () => client!,
    app,
  };
}

test("a first-load outage confirmed by the probe recovers the requested protected route", async () => {
  vi.useFakeTimers();
  const f = await fixture(() => 502);
  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  expect(screen.getByRole("dialog", { name: "Finding our way back" })).toBeTruthy();
  f.fail(healthy);
  await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
  // The router's native minimum pending time starts when recovery reloads it.
  await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
  expect(screen.getByRole("heading", { name: "Requested page" })).toBeTruthy();
  expect(screen.getByText("row-1")).toBeTruthy();
  expect(f.app.router.state.location.pathname).toBe("/connection-test");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByText("Something went wrong")).toBeNull();
});

test("a failed CSRF bootstrap on a healthy server neither blocks the page nor opens the dialog", async () => {
  let failed = false;
  const f = await fixture(request => {
    if (failed || request !== "csrf") return undefined;
    failed = true;
    return 502;
  });
  expect(await screen.findByRole("heading", { name: "Requested page" })).toBeTruthy();
  await waitFor(() => expect(f.count("AngeeConnection")).toBe(1));
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("a failing read on a healthy server stays an ordinary error without the dialog or a refresh loop", async () => {
  vi.useFakeTimers();
  const f = await fixture();
  await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
  expect(screen.getByText("row-1")).toBeTruthy();
  f.fail(request => request.includes("ConnectionRows") ? 504 : undefined);
  fireEvent.click(screen.getByRole("button", { name: "Refresh rows" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
  const reads = f.count("ConnectionRows");
  await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
  expect(f.count("ConnectionRows")).toBe(reads);
  expect(f.count("AngeeConnection")).toBeGreaterThan(0);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getAllByText("Something went wrong").length).toBeGreaterThan(0);
});

test("mid-session outage has one wall, no repeated traffic or read toasts, refreshes refused reads and reports the refused write", async () => {
  const f = await fixture();
  await screen.findByText("row-1");
  const draft = screen.getByLabelText("Draft");
  fireEvent.change(draft, { target: { value: "Keep my edits" } });
  f.fail(() => 502);
  fireEvent.click(screen.getByRole("button", { name: "Refresh rows" }));
  await screen.findByRole("dialog", { name: "Finding our way back" });
  const requestsWhileDown = f.requests.length;
  await act(async () => {
    await f.client().refetchQueries({ queryKey: ["angee", "authored"] });
    await f.client().refetchQueries({ queryKey: ["angee", "authored"] });
  });
  fireEvent.click(screen.getByRole("button", { name: "Save draft", hidden: true }));
  await waitFor(() => expect(f.client().getMutationCache().getAll()
    .some(write => write.state.error instanceof ServerUnavailableError)).toBe(true));
  expect(f.requests).toHaveLength(requestsWhileDown);
  expect(screen.getAllByRole("dialog")).toHaveLength(1);
  expect(screen.queryByText("Something went wrong")).toBeNull();
  const reads = f.count("ConnectionRows");
  f.fail(healthy);
  await act(async () => {
    await f.client().cancelQueries({ queryKey: SERVER_CONNECTION_QUERY_KEY });
    await f.client().refetchQueries({ queryKey: SERVER_CONNECTION_QUERY_KEY });
  });
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(screen.getByLabelText("Draft")).toBe(draft);
  expect((draft as HTMLInputElement).value).toBe("Keep my edits");
  await waitFor(() => expect(f.count("ConnectionRows")).toBeGreaterThan(reads));
  expect((await screen.findAllByText("A change was not saved")).length).toBeGreaterThan(0);
  expect(f.count("ConnectionSave")).toBe(0);
  expect(screen.queryByText("Something went wrong")).toBeNull();
});

test("identity settles after an ordinary failure and stays resolving only while the server is unavailable", async () => {
  const f = await fixture(request => request.includes("current_user") ? 500 : undefined, { requireAuth: false });
  await waitFor(() => expect(screen.getByLabelText("Session").textContent).toBe("anonymous"));
  expect(screen.queryByRole("dialog")).toBeNull();
  f.fail(() => 502);
  fireEvent.click(screen.getByRole("button", { name: "Refresh rows" }));
  await screen.findByRole("dialog", { name: "Finding our way back" });
  // Refetch identity while unavailable; the probe keeps its own schedule.
  await act(async () => { await f.client().invalidateQueries({ predicate: query => query.queryKey[1] !== SERVER_CONNECTION_QUERY_KEY[1] }); });
  await waitFor(() => expect(screen.getByLabelText("Session").textContent).toBe("resolving"));
  f.fail(healthy);
  await act(async () => {
    await f.client().cancelQueries({ queryKey: SERVER_CONNECTION_QUERY_KEY });
    await f.client().refetchQueries({ queryKey: SERVER_CONNECTION_QUERY_KEY });
  });
  await waitFor(() => expect(screen.getByLabelText("Session").textContent).toBe("authenticated"));
  expect(screen.queryByRole("dialog")).toBeNull();
});
