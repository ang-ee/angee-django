// @vitest-environment happy-dom
import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { fetchExchange, useClient } from "urql";
import { describe, expect, test, vi } from "vitest";

import {
  useLoginWithPassword,
  useLogout,
  useRuntimeAuthState,
} from "./auth-hooks";
import { GraphQLClientProvider } from "./graphql-provider";

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

interface AuthResponses {
  currentUser?: unknown;
  login?: unknown;
  logout?: unknown;
}

function authFetch(responses: AuthResponses): typeof globalThis.fetch {
  const fetch = vi.fn(async (url: string, init: RequestInit) => {
    if (String(url).includes("/csrf/")) return json({ token: "t" });
    const body = JSON.parse(String(init.body)) as { query: string };
    if (body.query.includes("currentUser"))
      return json({ data: { currentUser: responses.currentUser ?? null } });
    if (body.query.includes("login(")) return json({ data: { login: responses.login } });
    if (body.query.includes("logout")) return json({ data: { logout: responses.logout } });
    return json({ data: {} });
  });
  return fetch as unknown as typeof globalThis.fetch;
}

function wrapperWith(fetch: typeof globalThis.fetch) {
  // Stable config so a reset (and only a reset) rebuilds the clients.
  const config = {
    public: { url: "/graphql/", fetch, exchanges: [fetchExchange] },
  };
  return ({ children }: { children: ReactNode }) =>
    createElement(GraphQLClientProvider, { config, schema: "public", children });
}

const USER = {
  id: "u1",
  username: "ada",
  email: "ada@example.com",
  isStaff: true,
  isSuperuser: false,
  roles: ["admin"],
};

describe("useRuntimeAuthState", () => {
  test("maps a resolved currentUser to an authenticated state", async () => {
    const { result } = renderHook(() => useRuntimeAuthState(), {
      wrapper: wrapperWith(authFetch({ currentUser: USER })),
    });
    await waitFor(() => expect(result.current.fetching).toBe(false));
    expect(result.current.auth.status).toBe("authenticated");
    expect(result.current.auth.user?.username).toBe("ada");
    expect(result.current.auth.hasRole("admin")).toBe(true);
    expect(result.current.auth.user?.isStaff).toBe(true);
  });

  test("maps a null currentUser to an anonymous state", async () => {
    const { result } = renderHook(() => useRuntimeAuthState(), {
      wrapper: wrapperWith(authFetch({ currentUser: null })),
    });
    await waitFor(() => expect(result.current.fetching).toBe(false));
    expect(result.current.auth.status).toBe("anonymous");
    expect(result.current.auth.user).toBeNull();
  });
});

describe("useLoginWithPassword", () => {
  test("returns the payload and rebuilds the client on success", async () => {
    const fetch = authFetch({ login: { ok: true, error: null, user: USER } });
    const { result } = renderHook(
      () => ({ client: useClient(), api: useLoginWithPassword() }),
      { wrapper: wrapperWith(fetch) },
    );
    const before = result.current.client;
    let payload: { ok: boolean } | undefined;
    await act(async () => {
      payload = await result.current.api.login({ username: "ada", password: "x" });
    });
    expect(payload?.ok).toBe(true);
    expect(result.current.client).not.toBe(before);
  });

  test("does not rebuild the client when login fails", async () => {
    const fetch = authFetch({ login: { ok: false, error: "bad", user: null } });
    const { result } = renderHook(
      () => ({ client: useClient(), api: useLoginWithPassword() }),
      { wrapper: wrapperWith(fetch) },
    );
    const before = result.current.client;
    let payload: { ok: boolean; error?: string | null } | undefined;
    await act(async () => {
      payload = await result.current.api.login({ username: "ada", password: "x" });
    });
    expect(payload?.ok).toBe(false);
    expect(payload?.error).toBe("bad");
    expect(result.current.client).toBe(before);
  });
});

describe("useLogout", () => {
  test("rebuilds the client on success", async () => {
    const fetch = authFetch({ logout: { ok: true } });
    const { result } = renderHook(
      () => ({ client: useClient(), api: useLogout() }),
      { wrapper: wrapperWith(fetch) },
    );
    const before = result.current.client;
    await act(async () => {
      await result.current.api.logout();
    });
    expect(result.current.client).not.toBe(before);
  });
});
