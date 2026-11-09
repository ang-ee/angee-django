// @vitest-environment happy-dom
import { act, cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useAuthoredQuery, type TypedDocumentNode } from "@angee/refine";
import { parse } from "graphql";
import { afterEach, expect, test, vi } from "vitest";
import type { Root } from "react-dom/client";
import { createApp, PassthroughChrome } from "../create-app";
import { SERVER_CONNECTION_QUERY_KEY } from "../providers/connection";

const ROWS = parse("query ConnectionRows { rows { id } }") as TypedDocumentNode<{ rows: { id: string }[] }>;
const mounts: { root: Root; host: HTMLElement }[] = [];
afterEach(() => {
  mounts.splice(0).forEach(({ root, host }) => { act(() => root.unmount()); host.remove(); });
  cleanup();
  vi.useRealTimers();
});

async function fixture(initialFailure?: "csrf" | "graphql") {
  let down = initialFailure !== undefined;
  let client: QueryClient | undefined;
  const requests: string[] = [];
  const fetch: typeof globalThis.fetch = async (_input, init) => {
    const query = init?.body ? String(init.body) : "csrf";
    requests.push(query);
    if (down && (initialFailure !== "graphql" || query !== "csrf")) {
      // A first-load outage that clears by the first safe recovery probe.
      if (initialFailure) down = false;
      return new Response("Unavailable", { status: 502 });
    }
    return Response.json(query === "csrf" ? { token: "fixture", cookieName: null } : { data: {
      __typename: "Query",
      current_user: { id: "person-1", username: "fixture", firstName: "", lastName: "", email: "", isStaff: false, isActive: true, preferences: {}, roleRefs: [] },
      rows: [{ id: "row-1" }],
    } });
  };
  function Page() {
    client = useQueryClient();
    const read = useAuthoredQuery(ROWS, {});
    return <>
      <h1>Requested page</h1>
      <input aria-label="Draft" defaultValue="Unsaved changes" />
      <button onClick={() => { void read.refetch(); }}>Refresh rows</button>
      <span>{read.data?.rows[0]?.id}</span>
    </>;
  }
  history.replaceState(null, "", "/connection-test");
  const host = document.createElement("div");
  document.body.append(host);
  const app = createApp({
    addons: [{ id: "connection-test", routes: [{ name: "connection-test", path: "/connection-test", layout: "private", component: Page }] }],
    layouts: { private: { chrome: PassthroughChrome, requireAuth: true, schema: "public" } },
    schemas: { public: { url: "/graphql/public/", fetch } },
  });
  let root!: Root;
  await act(async () => { root = app.mount(host); });
  mounts.push({ root, host });
  return { requests, setDown: (value: boolean) => { down = value; }, client: () => client!, app };
}

test("a first-load auth outage recovers the requested protected route", async () => {
  const f = await fixture("graphql");
  expect(await screen.findByRole("heading", { name: "Requested page" })).toBeTruthy();
  expect(await screen.findByText("row-1")).toBeTruthy();
  expect(f.requests[0]).toBe("csrf");
  expect(f.requests.some(request => request.includes("AngeeConnection"))).toBe(true);
  expect(f.app.router.state.location.pathname).toBe("/connection-test");
  expect(screen.queryByText("Something went wrong")).toBeNull();
});

test("a cold CSRF outage does not starve its recovery bootstrap", async () => {
  vi.useFakeTimers();
  const f = await fixture("csrf");
  await act(async () => { await vi.advanceTimersByTimeAsync(5_100); });
  // The router's native minimum pending time starts when recovery reloads it.
  await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
  expect(screen.getByRole("heading", { name: "Requested page" })).toBeTruthy();
  expect(f.requests.filter(request => request.includes("AngeeConnection"))).toHaveLength(1);
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("mid-session outage has one wall, no repeated traffic or toasts, and refreshes authored reads in place", async () => {
  const f = await fixture();
  await screen.findByText("row-1");
  const draft = screen.getByLabelText("Draft");
  fireEvent.change(draft, { target: { value: "Keep my edits" } });
  f.setDown(true);
  fireEvent.click(screen.getByRole("button", { name: "Refresh rows" }));
  await screen.findByRole("dialog", { name: "Finding our way back" });
  const requestsWhileDown = f.requests.length;
  await act(async () => {
    await f.client().refetchQueries({ queryKey: ["angee", "authored"] });
    await f.client().refetchQueries({ queryKey: ["angee", "authored"] });
  });
  expect(f.requests).toHaveLength(requestsWhileDown);
  expect(screen.getAllByRole("dialog")).toHaveLength(1);
  expect(screen.queryByText("Something went wrong")).toBeNull();
  f.setDown(false);
  await act(async () => {
    await f.client().cancelQueries({ queryKey: SERVER_CONNECTION_QUERY_KEY });
    await f.client().refetchQueries({ queryKey: SERVER_CONNECTION_QUERY_KEY });
  });
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(screen.getByLabelText("Draft")).toBe(draft);
  expect((draft as HTMLInputElement).value).toBe("Keep my edits");
  expect(f.requests.filter(request => request.includes("ConnectionRows")).length).toBeGreaterThan(1);
  expect(screen.queryByText("Something went wrong")).toBeNull();
});
