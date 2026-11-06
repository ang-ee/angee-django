// @vitest-environment happy-dom
import { act, cleanup, screen } from "@testing-library/react";
import { useGetIdentity, useLogin, useLogout } from "@refinedev/core";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { parse } from "graphql";
import { afterEach, expect, test, vi } from "vitest";
import * as refine from "@angee/refine";

import { createApp, PassthroughChrome } from "./create-app";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const read = parse("query AcceptedRead { accepted }") as refine.TypedDocumentNode<{ accepted: string }, Record<string, never>>;
const write = parse("mutation HeldWrite { accepted }") as refine.TypedDocumentNode<{ accepted: string }, Record<string, never>>;
const person = (id: string) => ({ id, username: id, firstName: id, lastName: "", email: "", isStaff: false, isActive: true, preferences: {}, roleRefs: [] });
function gate() {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => { release = resolve; });
  return { promise, release };
}

test.each([
  { websocket: false, held: "response" }, { websocket: true, held: "response" },
  { websocket: false, held: "acceptance" }, { websocket: true, held: "acceptance" },
])("actor-A $held cannot populate actor-B cache after native logout/login (websocket=$websocket)", async ({ websocket, held }) => {
  let actor: string | null = "actor-a";
  const release = gate();
  const transportStarted = gate();
  const acceptanceStarted = gate();
  const createWebsocket = vi.spyOn(refine, "createAngeeHasuraLiveProvider").mockImplementation((options) =>
    refine.createAngeeChangeLiveProvider({ subscribe: () => () => undefined, on: () => () => undefined } as never,
      options.resources ?? [], { queryClient: options.queryClient }));
  const fetch: typeof globalThis.fetch = async (_input, init) => {
    const { query, variables } = JSON.parse(String(init?.body)) as { query: string; variables?: { username?: string } };
    if (query.includes("HeldWrite")) {
      transportStarted.release();
      if (held === "response") await release.promise;
      return Response.json({ data: { accepted: "actor-a-private" } });
    }
    if (query.includes("AngeeLogout")) { actor = null; return Response.json({ data: { logout: true } }); }
    if (query.includes("AngeeLogin")) {
      actor = variables?.username ?? null;
      return Response.json({ data: { login: { ok: true, user: actor ? person(actor) : null } } });
    }
    return Response.json({ data: { current_user: actor ? person(actor) : null, real_user: null } });
  };
  let client!: QueryClient;
  let begin!: () => Promise<unknown>;
  let login!: (username: string) => Promise<unknown>;
  let logout!: () => Promise<unknown>;
  let acceptCurrent!: (value: string) => Promise<void>;
  function Page() {
    client = useQueryClient();
    const identity = useGetIdentity<{ id: string } | null>();
    const signIn = useLogin<{ username: string; password: string }>();
    const signOut = useLogout();
    const accept = refine.useSetAuthoredQueryData();
    const [mutate] = refine.useAuthoredMutation(write, {
      dataProviderName: "public",
      acceptResponse: async (data) => {
        acceptanceStarted.release();
        if (held === "acceptance") await release.promise;
        if (!data) throw new Error("Accepted result missing.");
        await accept(read, {}, data, { dataProviderName: "public" });
      },
    });
    begin = () => mutate({});
    login = (username) => signIn.mutateAsync({ username, password: "fixture-secret" });
    logout = () => signOut.mutateAsync({ redirectPath: false });
    acceptCurrent = async (accepted) => { await accept(read, {}, { accepted }, { dataProviderName: "public" }); };
    return <span>{identity.data?.id ?? "anonymous"}</span>;
  }
  history.replaceState(null, "", "/acceptance");
  const app = createApp({
    addons: [{ id: "acceptance", routes: [{ name: "acceptance", path: "/acceptance", layout: "public", component: Page }] }],
    layouts: { public: { schema: "public", chrome: PassthroughChrome, requireAuth: false } },
    schemas: {
      public: { url: "https://example.test/graphql/public/", fetch, auth: (base) => base },
      console: { url: "https://example.test/graphql/console/", fetch, auth: (base) => base, live: websocket },
    },
  });
  const host = document.createElement("div"); document.body.append(host);
  const mounted = app.mount(host);
  let oldCompletion: Promise<unknown> | undefined;
  try {
    await screen.findByText("actor-a");
    act(() => { oldCompletion = begin().catch((error: unknown) => error); });
    await transportStarted.promise;
    if (held === "acceptance") await acceptanceStarted.promise;
    await act(async () => { await logout(); });
    await screen.findByText("anonymous");
    await act(async () => { await login("actor-b"); });
    await screen.findByText("actor-b");
    await act(async () => { release.release(); await oldCompletion; });
    const key = refine.authoredQueryKey(read, {}, "public");
    expect(client.getQueryData(key)).toBeUndefined();
    await act(async () => { await acceptCurrent("actor-b-private"); });
    expect(client.getQueryData(key)).toEqual({ accepted: "actor-b-private" });
    expect(createWebsocket).toHaveBeenCalledTimes(websocket ? 1 : 0);
  } finally {
    release.release();
    await oldCompletion;
    act(() => mounted.unmount()); host.remove(); client?.clear();
  }
});
