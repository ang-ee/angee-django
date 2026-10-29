// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRouter, RouterContextProvider } from "@tanstack/react-router";
import { ResourceQuery } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import type { RefineTestDataProvider } from "@angee/refine/testing";
import { AppRuntimeProvider, ModalsHost, ToastProvider, defaultWidgets, type WidgetDefinition } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import { afterEach, expect, test, vi } from "vitest";

import { KnowledgePageView } from "./KnowledgePageView";
import { KnowledgePages, KnowledgeUpdatePageBody, PAGE_MODEL, type KnowledgePageDetail } from "./data/documents";

const resource = testDataResource(PAGE_MODEL, {
  query: ResourceQuery.forRows({ fields: { id: { scalar: "ID" }, title: { scalar: "String" }, vault: { scalar: "ID" },
    parent: { scalar: "ID" }, is_trashed: { scalar: "Boolean" }, navigation_root: { scalar: "Boolean" } } }).contract,
  fields: [{ name: "title", kind: "scalar", scalar: "String", readable: true, updatable: true,
    creatable: true, requiredOnCreate: true, aggregatable: false }],
});
const { Provider, createClient, clearClients } = createUiTestProviders({ resources: [resource], apiUrl: "test://knowledge-page" });
afterEach(() => { cleanup(); clearClients(); });

// Keep the real form, RHF, query cache and mutation hooks. Only the widget's text
// control is simplified; CodeMirror has its own interaction coverage.
const markdownWidget: WidgetDefinition<string> = {
  edit: ({ value, onChange, onCommit }) => <textarea aria-label="Page body" value={value ?? ""}
    onChange={(event) => onChange?.(event.currentTarget.value)} onBlur={onCommit} />,
  read: ({ value }) => <p>{value}</p>,
};

function detail(overrides: Partial<KnowledgePageDetail> = {}): KnowledgePageDetail {
  return { id: "pg_guide", title: "Guide", kind: "note", permissions: ["write"], icon: "", vault: "vlt_reference",
    parent: null, vault_label: "Reference", has_children: false, is_trashed: false, ancestors: [], wikilinks: [], updated_at: "2026-01-01T12:00:00Z", created_by_label: "Author", backlinks: [],
    markdown: { body: "Published instructions", body_hash: "original-hash", word_count: 2 }, ...overrides };
}

async function fixture(initial = detail()) {
  let record = initial;
  let failure: Error | null = null;
  let release: (() => void) | undefined;
  let pending: Promise<void> | undefined;
  let readPending: Promise<void> | undefined;
  let releaseRead: (() => void) | undefined;
  const client = createClient({ defaultOptions: { queries: { retry: false } } });
  const writes = vi.fn<(variables: Record<string, unknown>) => void>();
  const custom: NonNullable<RefineTestDataProvider["custom"]> = async ({ meta }) => {
    if (meta?.gqlQuery === KnowledgePages) return { data: { pages: [], pages_aggregate: { aggregate: { count: 0 } } } };
    if (meta?.gqlMutation !== KnowledgeUpdatePageBody) {
      const snapshot = record;
      if (readPending) await readPending;
      return { data: { pages_by_pk: snapshot } };
    }
    const variables: Record<string, unknown> = meta.gqlVariables ?? {};
    writes(variables);
    if (pending) await pending;
    if (failure) throw failure;
    if (variables.expected_hash !== record.markdown?.body_hash) {
      return { data: { update_page_body: { ok: false, error_code: "STALE_BODY", error: "Body hash is stale", markdown: null } } };
    }
    record = { ...record, title: String(variables.title ?? record.title), markdown: {
      body: String(variables.body), body_hash: `hash-${writes.mock.calls.length}`, word_count: 2,
    } };
    return { data: { update_page_body: { ok: true, error: null, error_code: null, markdown: { ...record.markdown, title: record.title } } } };
  };
  const update = vi.fn<NonNullable<RefineTestDataProvider["update"]>>(async ({ variables }) => {
    record = { ...record, ...(variables as Partial<KnowledgePageDetail>) };
    return { data: record };
  });
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  const renderPage = (pageId = record.id) => <Provider dataProvider={{ custom, update }} queryClient={client}>
    <RouterContextProvider router={router}><ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: {
      ...defaultWidgets, "markdown.editor": markdownWidget,
    } }}><KnowledgePageView pageId={pageId} />
    </AppRuntimeProvider></ToastProvider></ModalsHost></RouterContextProvider>
  </Provider>;
  const view = render(renderPage());
  await screen.findByDisplayValue(initial.title);
  return {
    writes, update, client,
    get record() { return record; },
    remote: async (next: KnowledgePageDetail) => {
      record = next;
      await act(async () => { await client.invalidateQueries(); });
    },
    deferReads: () => { readPending = new Promise<void>((resolve) => { releaseRead = resolve; }); },
    resolveReads: async () => { await act(async () => { releaseRead?.(); await readPending; }); readPending = undefined; },
    fail: (error: Error | null) => { failure = error; },
    defer: () => { pending = new Promise<void>((resolve) => { release = resolve; }); },
    resolve: async () => { await act(async () => { release?.(); await pending; }); pending = undefined; },
    navigate: (next: KnowledgePageDetail) => { record = next; view.rerender(renderPage(next.id)); },
  };
}

function edit(label: string, value: string) {
  fireEvent.change(screen.getByRole("textbox", { name: label }), { target: { value } });
}

async function save() {
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));
}

test("native form saves title and body together and advances the hash for the next save", async () => {
  const f = await fixture();
  edit("Page title", "Draft title");
  edit("Page body", "Draft body");
  fireEvent.blur(screen.getByRole("textbox", { name: "Page body" }));
  expect(f.writes).not.toHaveBeenCalled();
  expect(screen.getAllByRole("button", { name: "Save" })).toHaveLength(1);
  await save();
  await waitFor(() => expect(screen.queryByRole("button", { name: "Save" })).toBeNull());
  expect(f.writes).toHaveBeenLastCalledWith({ page: "pg_guide", title: "Draft title", body: "Draft body", expected_hash: "original-hash" });
  expect(f.update).not.toHaveBeenCalled();
  edit("Page body", "Second body");
  await save();
  await waitFor(() => expect(f.record.markdown?.body).toBe("Second body"));
  expect(f.writes).toHaveBeenLastCalledWith(expect.objectContaining({ expected_hash: "hash-1" }));
});

test("Discard restores the native form baseline without a write", async () => {
  const f = await fixture();
  edit("Page title", "Discard title");
  edit("Page body", "Discard body");
  fireEvent.click(await screen.findByRole("button", { name: "Discard" }));
  await screen.findByDisplayValue("Published instructions");
  expect(screen.getByDisplayValue("Guide")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  expect(f.writes).not.toHaveBeenCalled();
});

test("a stale body keeps the draft and uses the native reload-saved recovery", async () => {
  const f = await fixture();
  edit("Page body", "My draft");
  await f.remote(detail({ markdown: { body: "Remote body", body_hash: "remote-hash", word_count: 2 } }));
  await save();
  await screen.findByText("Body hash is stale");
  expect(screen.getByDisplayValue("My draft")).toBeTruthy();
  expect(f.writes).toHaveBeenLastCalledWith(expect.objectContaining({ expected_hash: "original-hash" }));
  fireEvent.click(screen.getByRole("button", { name: "Reload saved record" }));
  await screen.findByDisplayValue("Remote body");
  edit("Page body", "Resolved body");
  await save();
  await waitFor(() => expect(f.record.markdown?.body).toBe("Resolved body"));
});

test("transport failure preserves both fields for a normal Save retry", async () => {
  const f = await fixture();
  f.fail(new Error("Offline"));
  edit("Page title", "Retry title");
  edit("Page body", "Retry body");
  await save();
  await screen.findByText("Offline");
  expect(screen.getByDisplayValue("Retry title")).toBeTruthy();
  expect(screen.getByDisplayValue("Retry body")).toBeTruthy();
  f.fail(null);
  await save();
  await waitFor(() => expect(f.record.title).toBe("Retry title"));
});

test("a body-only save preserves a concurrent title change", async () => {
  const f = await fixture();
  edit("Page body", "My body");
  await f.remote(detail({ title: "Renamed remotely" }));
  await save();
  await waitFor(() => expect(f.record.markdown?.body).toBe("My body"));
  expect(f.record.title).toBe("Renamed remotely");
  expect(f.writes).toHaveBeenLastCalledWith({ page: "pg_guide", body: "My body", expected_hash: "original-hash" });
});

test("typing during a pending save remains dirty against the accepted hash", async () => {
  const f = await fixture();
  f.defer();
  edit("Page body", "First body");
  await save();
  await waitFor(() => expect(f.writes).toHaveBeenCalledTimes(1));
  edit("Page body", "Later body");
  await f.resolve();
  await waitFor(() => expect(f.record.markdown?.body).toBe("First body"));
  expect(screen.getByDisplayValue("Later body")).toBeTruthy();
  await save();
  await waitFor(() => expect(f.record.markdown?.body).toBe("Later body"));
  expect(f.writes).toHaveBeenLastCalledWith(expect.objectContaining({ expected_hash: "hash-1" }));
});

test("revoked write permission makes the native form read-only", async () => {
  const f = await fixture();
  await f.remote(detail({ permissions: [] }));
  await screen.findByRole("heading", { name: "Guide" });
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.getByText("Published instructions")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
});

test("folders use generated CRUD and the existing empty child-page surface", async () => {
  const f = await fixture(detail({ kind: "folder", markdown: null }));
  expect(await screen.findByText("No pages here yet")).toBeTruthy();
  expect(screen.queryByRole("textbox", { name: "Page body" })).toBeNull();
  edit("Page title", "Renamed folder");
  await save();
  await waitFor(() => expect(f.update).toHaveBeenCalledTimes(1));
  expect(f.writes).not.toHaveBeenCalled();
  expect(f.record.title).toBe("Renamed folder");
});

test("changing page identity mounts a fresh native form", async () => {
  const f = await fixture();
  edit("Page body", "Previous page draft");
  f.navigate(detail({ id: "pg_other", title: "Other page" }));
  await screen.findByDisplayValue("Other page");
  expect(screen.getByDisplayValue("Published instructions")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
});

test("the accepted response acknowledges Save while derived refresh is held", async () => {
  const f = await fixture();
  f.deferReads();
  edit("Page title", "Accepted title");
  edit("Page body", "Accepted body");
  await save();
  await waitFor(() => expect(screen.queryByRole("button", { name: "Save" })).toBeNull());
  expect(f.client.isFetching()).toBeGreaterThan(0);
  expect(screen.getByDisplayValue("Accepted title")).toBeTruthy();
  expect(screen.getByDisplayValue("Accepted body")).toBeTruthy();
  edit("Page body", "Next accepted body");
  await save();
  await waitFor(() => expect(screen.queryByRole("button", { name: "Save" })).toBeNull());
  expect(f.writes).toHaveBeenLastCalledWith(expect.objectContaining({ expected_hash: "hash-1" }));
  await f.resolveReads();
  await waitFor(() => expect(f.client.isFetching()).toBe(0));
  expect(screen.getByDisplayValue("Next accepted body")).toBeTruthy();
});

test("a read started before acceptance cannot replay the old body after Save", async () => {
  const f = await fixture();
  f.deferReads();
  act(() => { void f.client.invalidateQueries(); });
  await waitFor(() => expect(f.client.isFetching()).toBeGreaterThan(0));
  edit("Page body", "Accepted after old read");
  await save();
  await waitFor(() => expect(screen.queryByRole("button", { name: "Save" })).toBeNull());
  await f.resolveReads();
  await waitFor(() => expect(f.client.isFetching()).toBe(0));
  expect(screen.getByDisplayValue("Accepted after old read")).toBeTruthy();
});
