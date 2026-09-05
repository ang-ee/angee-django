// @vitest-environment happy-dom

import { act, waitFor } from "@testing-library/react";
import { useDataProvider, useLogin, useLogout } from "@refinedev/core";
import { isCancelledError, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { authoredQueryOptions, useAuthoredQuery, type TypedDocumentNode } from "@angee/refine";
import { parse } from "graphql";
import type { Root } from "react-dom/client";
import { afterEach, expect, test } from "vitest";
import { createApp, PassthroughChrome } from "../create-app";
import { useAuth, type LoginCredentials } from "../providers/auth";

const ROWS = parse(`
  query AuthCacheRows($kind: String!) { rows(kind: $kind) { id } }
`) as TypedDocumentNode<{ rows: { id: string }[] }, { kind: string }>;
type Action = "login" | "logout";
type Outcome = "success" | "denied" | "error";
type ProbeState = {
  client: QueryClient;
  provider: ReturnType<typeof useDataProvider>;
  login: ReturnType<typeof useLogin<LoginCredentials>>;
  logout: ReturnType<typeof useLogout>;
};
const mounts: { root: Root; host: HTMLElement; state: () => ProbeState }[] = [];

afterEach(() => {
  for (const mounted of mounts.splice(0)) {
    act(() => mounted.root.unmount());
    mounted.state().client.clear();
    mounted.host.remove();
  }
});

function response(data: unknown): Response {
  return new Response(JSON.stringify({ data }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

async function fixture(action: Action, outcome: Outcome, observePending = false) {
  let state!: ProbeState;
  let actor = "previous-session";
  let resolvePending!: (value: Response) => void;
  const pendingResponse = new Promise<Response>((resolve) => { resolvePending = resolve; });
  let pendingStarted = false;
  const authCalls: string[] = [];

  function user() {
    return actor === "anonymous" ? null : {
      id: actor, username: actor, firstName: "", lastName: "", email: "",
      isStaff: false, isActive: true, preferences: {}, roleRefs: [],
    };
  }

  const fetch: typeof globalThis.fetch = async (_input, init) => {
    if (!init?.body) return response({}); // Native session CSRF bootstrap.
    const { query, variables } = JSON.parse(String(init.body)) as {
      query: string; variables?: { kind?: string };
    };
    if (query.includes("AngeeCurrentUser")) return response({ current_user: user() });
    if (query.includes("AuthCacheRows")) {
      if (variables?.kind === "pending" && !pendingStarted) {
        pendingStarted = true;
        // Deliberately ignore cancellation: Hasura's fetch can settle late.
        return pendingResponse;
      }
      return response({ rows: [{ id: `${actor}:${variables?.kind}` }] });
    }
    if (!query.includes("AngeeLogin") && !query.includes("AngeeLogout")) {
      throw new Error("Unexpected GraphQL operation in auth cache fixture");
    }
    const operation = query.includes("AngeeLogin") ? "login" : "logout";
    authCalls.push(operation);
    if (outcome === "error") {
      return new Response(JSON.stringify({ errors: [{ message: "Fixture auth failure" }] }), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
    }
    if (outcome === "success") actor = operation === "login" ? "next-session" : "anonymous";
    return response(operation === "login"
      ? { login: { ok: outcome === "success", user: user() } }
      : { logout: outcome === "success" });
  };

  function PendingObserver() {
    const pending = useAuthoredQuery(ROWS, { kind: "pending" }, { enabled: false });
    return <span>{pending.data?.rows[0]?.id}</span>;
  }

  function Probe() {
    state = {
      client: useQueryClient(), provider: useDataProvider(),
      login: useLogin<LoginCredentials>(), logout: useLogout(),
    };
    const auth = useAuth();
    const rows = useAuthoredQuery(ROWS, { kind: "observed" });
    return <output data-testid="auth-cache-probe">
      <span data-testid="auth-identity">{auth.user?.id ?? "anonymous"}</span>
      <span data-testid="observed-rows">{rows.data?.rows[0]?.id}</span>
      {observePending && <PendingObserver />}
    </output>;
  }

  const host = document.createElement("div");
  document.body.append(host);
  history.replaceState(null, "", "/auth-cache");
  const app = createApp({
    addons: [{ id: "auth-cache", routes: [{ name: "auth-cache", path: "/auth-cache", layout: "public", component: Probe }] }],
    layouts: { public: { chrome: PassthroughChrome, requireAuth: false, schema: "public" } },
    schemas: { public: { url: "https://auth-cache.test/graphql/public/", fetch } },
    home: "/auth-cache",
  });
  let root!: Root;
  await act(async () => { root = app.mount(host); });
  mounts.push({ root, host, state: () => state });
  await waitFor(() => {
    expect(host.querySelector('[data-testid="auth-identity"]')?.textContent).toBe("previous-session");
    expect(host.querySelector('[data-testid="observed-rows"]')?.textContent).toBe("previous-session:observed");
  });
  const options = (kind: string) => authoredQueryOptions(state.client, state.provider, "public", ROWS, { kind });
  const authenticate = () => action === "login"
    ? state.login.mutateAsync({ username: "fixture", password: "fixture" })
    : state.logout.mutateAsync({ redirectPath: false });
  return { state: () => state, host, options, authenticate, authCalls,
    pendingStarted: () => pendingStarted,
    finishOldRequest: () => resolvePending(response({ rows: [{ id: "previous-session:pending" }] })),
  };
}

for (const action of ["login", "logout"] as const) {
  for (const observePending of [false, true]) {
    test(`createApp successful ${action} clears session rows and discards late ${observePending ? "observed disabled" : "unobserved"} query results`, async () => {
      const f = await fixture(action, "success", observePending);
      const client = f.state().client;
      const cached = f.options("cached");
      const pending = f.options("pending");
      await client.fetchQuery(cached);
      expect(client.getQueryData(cached.queryKey)).toEqual({ rows: [{ id: "previous-session:cached" }] });
      const oldRequest = client.fetchQuery(pending).catch((error: unknown) => error);
      await waitFor(() => expect(f.pendingStarted()).toBe(true));
      expect(client.getQueryCache().find({ queryKey: pending.queryKey })?.getObserversCount()).toBe(observePending ? 1 : 0);

      await act(async () => { expect((await f.authenticate()).success).toBe(true); });
      expect(f.authCalls).toEqual([action]);
      expect(client.getQueryData(cached.queryKey)).toBeUndefined();
      expect(client.getQueryData(pending.queryKey)).toBeUndefined();
      expect(isCancelledError(await oldRequest)).toBe(true);

      // The same key can now belong to the new session while the old transport
      // is still outstanding. A late old response must not overwrite it.
      const next = { rows: [{ id: `${action === "login" ? "next-session" : "anonymous"}:pending` }] };
      await expect(client.fetchQuery(f.options("pending"))).resolves.toEqual(next);
      await act(async () => {
        f.finishOldRequest();
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
      expect(client.getQueryData(pending.queryKey)).toEqual(next);
      expect(client.getQueryData(cached.queryKey)).toBeUndefined();
      const actor = action === "login" ? "next-session" : "anonymous";
      await waitFor(() => {
        expect(f.host.querySelector('[data-testid="auth-identity"]')?.textContent).toBe(actor);
        expect(f.host.querySelector('[data-testid="observed-rows"]')?.textContent).toBe(`${actor}:observed`);
        expect(f.host.textContent).not.toContain("previous-session");
      });
    });
  }

  for (const outcome of ["denied", "error"] as const) {
    test(`createApp ${outcome} ${action} preserves same-session cached and pending rows`, async () => {
      const f = await fixture(action, outcome);
      const client = f.state().client;
      const cached = f.options("cached");
      const pending = f.options("pending");
      const rows = await client.fetchQuery(cached);
      const cachedQuery = client.getQueryCache().find({ queryKey: cached.queryKey });
      const oldRequest = client.fetchQuery(pending);
      await waitFor(() => expect(f.pendingStarted()).toBe(true));

      await act(async () => { expect((await f.authenticate()).success).toBe(false); });
      expect(f.authCalls).toEqual([action]);
      expect(client.getQueryCache().find({ queryKey: cached.queryKey })).toBe(cachedQuery);
      expect(client.getQueryData(cached.queryKey)).toBe(rows);
      f.finishOldRequest();
      await expect(oldRequest).resolves.toEqual({ rows: [{ id: "previous-session:pending" }] });
      expect(client.getQueryData(pending.queryKey)).toEqual({ rows: [{ id: "previous-session:pending" }] });
      expect(f.host.textContent).toContain("previous-session");
    });
  }
}
