// @vitest-environment happy-dom
import { act, cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useSubscription } from "@refinedev/core";
import * as refine from "@angee/refine";
import { useRuntimeViewAs, type RuntimeViewAs } from "@angee/ui/runtime";
import { ConsoleLayout } from "@angee/ui/layouts/ConsoleLayout";
import { parse } from "graphql";
import { afterEach, expect, test, vi } from "vitest";
import { createApp } from "./create-app";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const rowsDocument = parse("query PreviewRows { rows }") as refine.TypedDocumentNode<{ rows: string[] }, Record<string, never>>;
const person = (id: string) => ({ id, username: id, firstName: id, lastName: "", email: "", isStaff: false, isActive: true, preferences: {}, roleRefs: [] });

test("enter/exit reset observed reads, remove inactive data and mutations, and suspend change delivery", async () => {
  const upstream = vi.fn(() => vi.fn());
  const live = refine.createAngeeChangeLiveProvider(
    { subscribe: upstream, on: vi.fn(() => () => undefined) } as never,
    [{ schemaName: "console", modelLabel: "notes.Note", roots: { list: "notes", changes: "noteChanged" } }],
  );
  const enabled = vi.spyOn(live, "setEnabled");
  vi.spyOn(refine, "createAngeeHasuraLiveProvider").mockReturnValue(live);
  let preview!: RuntimeViewAs;
  let client!: QueryClient;
  let rowGate: Promise<void> | undefined;
  let releaseRows!: () => void;
  const rowActors: (string | null)[] = [];
  const fetch: typeof globalThis.fetch = async (_input, init) => {
    const target = new Headers(init?.headers).get("X-Angee-View-As");
    const { query } = JSON.parse(String(init?.body)) as { query: string };
    if (query.includes("PreviewRows")) {
      rowActors.push(target);
      await rowGate;
      return Response.json({ data: { rows: [target ? "target row" : "real row"] } });
    }
    return Response.json({ data: {
      current_user: person(target ?? "real"), real_user: target ? person("real") : null,
      viewable_people: [{ id: "person", name: "Person" }],
    } });
  };
  function Page() {
    preview = useRuntimeViewAs();
    client = useQueryClient();
    const rows = refine.useAuthoredQuery(rowsDocument, {}, { models: ["notes.Note"] });
    useSubscription({ channel: "preview-test", types: ["*"], params: { models: ["notes.Note"] }, onLiveEvent: () => undefined });
    return <div data-testid="rows">{rows.data?.rows.join(",") ?? "loading"}</div>;
  }
  history.replaceState(null, "", "/preview");
  const app = createApp({
    addons: [{ id: "preview", routes: [
      { name: "preview", path: "/preview", component: Page },
      { name: "public", path: "/public-page", layout: "public", component: () => <span>Public page</span> },
    ] }],
    layouts: { console: { chrome: ConsoleLayout, requireAuth: false }, public: { schema: "public", requireAuth: false } }, defaultSchema: "console",
    schemas: {
      public: { url: "https://example.test/graphql/public/", fetch, auth: (base) => base },
      console: { url: "https://example.test/graphql/console/", fetch, auth: (base) => base, live: true },
    },
  });
  const host = document.createElement("div"); document.body.append(host);
  const root = app.mount(host);
  try {
    await waitFor(() => expect(preview.viewablePeople).toEqual([{ id: "person", name: "Person" }]));
    await screen.findByText("real row");
    client.setQueryData(["inactive-private-row"], "private");
    client.getMutationCache().build(client, { mutationKey: ["previous-write"] });
    rowGate = new Promise<void>((resolve) => { releaseRows = resolve; });
    // A late real-actor response must not repopulate the reset observer.
    const readsBeforeRefresh = rowActors.length;
    void client.invalidateQueries({ predicate: (query) => refine.authoredQueryReadsAnyModel(query.meta, ["notes.Note"]) });
    await waitFor(() => expect(rowActors.length).toBeGreaterThan(readsBeforeRefresh));
    const previousSubscriptions = upstream.mock.results.map((result) => result.value);
    fireEvent.click(screen.getByRole("button", { name: "User menu" }));
    fireEvent.click(await screen.findByLabelText("Preview as a person"));
    fireEvent.click(await screen.findByRole("option", { name: "Person" }));
    await waitFor(() => expect(preview.pending).toBe(false));
    expect(preview.currentUser?.id).toBe("person");
    expect(preview.realUser?.id).toBe("real");
    expect(screen.getByTestId("rows").textContent).not.toContain("real row");
    expect(client.getQueryData(["inactive-private-row"])).toBeUndefined();
    expect(client.getMutationCache().getAll()).toHaveLength(0);
    expect(enabled).toHaveBeenLastCalledWith(false);
    previousSubscriptions.forEach((dispose) => expect(dispose).toHaveBeenCalled());
    const pausedSubscriptions = upstream.mock.calls.length;
    act(() => releaseRows());
    await screen.findByText("target row");
    expect(upstream).toHaveBeenCalledTimes(pausedSubscriptions);
    await act(async () => { await app.router.navigate({ to: "/public-page" }); });
    await screen.findByText("Public page");
    expect(screen.getByRole("button", { name: "Exit preview" })).toBeTruthy();
    await act(async () => { await app.router.navigate({ to: "/preview" }); });
    await screen.findByText("target row");
    client.setQueryData(["inactive-target-row"], "target");
    rowGate = new Promise<void>((resolve) => { releaseRows = resolve; });
    fireEvent.click(screen.getByRole("button", { name: "Exit preview" }));
    await waitFor(() => expect(preview.viewAs).toBeNull());
    expect(screen.getByTestId("rows").textContent).not.toContain("target row");
    expect(client.getQueryData(["inactive-target-row"])).toBeUndefined();
    await waitFor(() => expect(enabled).toHaveBeenLastCalledWith(true));
    expect(upstream.mock.calls.length).toBeGreaterThan(pausedSubscriptions);
    act(() => releaseRows());
    await screen.findByText("real row");
    expect(rowActors).toContain("person");
    expect(rowActors.at(-1)).toBeNull();
  } finally { act(() => root.unmount()); host.remove(); client?.clear(); }
});
