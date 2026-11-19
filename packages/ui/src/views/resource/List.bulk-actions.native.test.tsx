// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRouter, RouterContextProvider } from "@tanstack/react-router";
import type { BaseKey, MetaQuery } from "@refinedev/core";
import { ResourceQuery, type DataResourceFieldMetadata, type DataResourceMetadata, type Row } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import type { TypedDocumentNode } from "@angee/refine";
import { gql } from "graphql-tag";
import { afterEach, expect, test, vi } from "vitest";

import { baseIcons } from "../../chrome/icon-registry";
import { ModalsHost, ToastProvider } from "../../feedback";
import { AppRuntimeProvider, type RuntimeViewAs } from "../../runtime";
import { createUiTestProviders } from "../../testing";
import { Column } from "../page";
import { collectionQuery } from "./collection-source";
import { List } from "./List";
import { ResourceViewProvider, useResourceView, type ResourceViewContextValue } from "./resource-view-context";

const flags: DataResourceFieldMetadata[] = [
  { name: "archived", kind: "scalar", scalar: "Boolean", readable: true, aggregatable: false,
    creatable: true, updatable: true, requiredOnCreate: false, archivable: true },
  { name: "removed", kind: "scalar", scalar: "Boolean", readable: true, aggregatable: false,
    creatable: false, updatable: false, requiredOnCreate: false, trashable: true },
];
const permissionField: DataResourceFieldMetadata = {
  name: "permissions", kind: "list", scalar: "String", readable: true, aggregatable: false,
  creatable: false, updatable: false, requiredOnCreate: false,
};
const query = ResourceQuery.forRows({ fields: {
  id: { scalar: "ID" }, title: { scalar: "String" }, archived: { scalar: "Boolean" }, removed: { scalar: "Boolean" },
} });
const resource = testDataResource("notes.Note", {
  resourceType: "notes/note", fields: flags, updateFields: ["archived"],
  roots: { aggregate: "notes_aggregate" },
  typeNames: { filter: "NoteBoolExp", order: "NoteOrderBy" }, query: query.contract,
});
const { Provider, clearClients } = createUiTestProviders({
  apiUrl: "test://bulk-actions", resources: [resource],
  queryClientConfig: { defaultOptions: { queries: { retry: false }, mutations: { retry: false } } },
});
afterEach(() => { cleanup(); clearClients(); });

const sourceDocument: TypedDocumentNode<{ rows: Row[] }, Record<string, never>> = gql`query SelectedNotes { rows { id title archived removed } }`;
const note = (id: string, archived = false, removed = false): Row => ({ id, title: `Note ${id}`, archived, removed });

async function fixture({ rows = [note("one"), note("two")], metadata = resource, source = false,
  preview, update: write, custom: authored }: {
  rows?: Row[];
  metadata?: DataResourceMetadata;
  source?: boolean;
  preview?: RuntimeViewAs;
  update?: (id: BaseKey | undefined, values: unknown) => Promise<{ data: Row }>;
  custom?: (variables: Record<string, unknown>) => Promise<unknown>;
} = {}) {
  const getList = vi.fn(async () => ({ data: rows, total: rows.length }));
  const update = vi.fn(async ({ id, variables }: { id?: BaseKey; variables?: unknown }) =>
    write ? write(id, variables) : { data: { id } });
  const custom = vi.fn(async ({ meta }: { meta?: MetaQuery }) => ({ data: source ? { rows }
    : authored ? await authored(meta?.gqlVariables ?? {})
      : { trash_record: { ok: true, message: "Moved." }, restore_record: { ok: true, message: "Restored." } } }));
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  let view!: ResourceViewContextValue;
  function Probe() { view = useResourceView(); return null; }
  const sourceRows = source ? { query, rows: collectionQuery({ document: sourceDocument, variables: () => ({}),
    select: (data) => ({ rows: data.rows, total: data.rows.length }) }) } : undefined;
  function Tree({ preview: currentPreview = preview }: { preview?: RuntimeViewAs }) {
    return <RouterContextProvider router={router}><Provider resources={[metadata]} dataProvider={{ getList, update, custom }}>
      <AppRuntimeProvider runtime={{ icons: baseIcons,
        ...(currentPreview ? { auth: { user: currentPreview.currentUser, status: "authenticated", hasRole: () => false, viewAs: currentPreview } } : {}),
      }}><ModalsHost><ToastProvider>
        <ResourceViewProvider resource={metadata.modelLabel} scope="local">
          <Probe />
          <List resource={metadata.modelLabel} scope="inherit" selectable source={sourceRows}>
            <Column field="title" />
          </List>
        </ResourceViewProvider>
      </ToastProvider></ModalsHost></AppRuntimeProvider>
    </Provider></RouterContextProvider>;
  }
  const mounted = render(<Tree />);
  await screen.findByText(String(rows[0]!.title));
  return { getList, update, custom, view: () => view,
    preview: (value: RuntimeViewAs) => mounted.rerender(<Tree preview={value} />),
    selectAll: () => act(() => view.setRowSelection(Object.fromEntries(rows.map((row) => [String(row.id), true])),
      (id) => rows.find((row) => row.id === id))),
  };
}

test.each([false, true])("%s archive state offers its inverse and updates each selected row once", async (archived) => {
  const f = await fixture({ rows: [note("one", archived), note("two", archived)] });
  for (const checkbox of screen.getAllByRole("checkbox", { name: "Select row" })) fireEvent.click(checkbox);
  const action = archived ? "Unarchive" : "Archive";
  expect(screen.queryByRole("button", { name: archived ? "Archive" : "Unarchive" })).toBeNull();
  const reads = f.getList.mock.calls.length;
  fireEvent.click(screen.getByRole("button", { name: action }));
  await screen.findByText("Updated archive status for 2 records.");
  expect(f.update).toHaveBeenCalledTimes(2);
  for (const id of ["one", "two"]) expect(f.update).toHaveBeenCalledWith(expect.objectContaining({
    resource: "notes", id, variables: { archived: !archived }, meta: expect.objectContaining({ fields: ["id"] }),
  }));
  expect(f.getList).toHaveBeenCalledTimes(reads + 1);
  expect(f.view().selectedRows).toEqual([]);
});

test("mixed archive selection offers both verbs and changes only the matching rows", async () => {
  const f = await fixture({ rows: [note("one"), note("two", true)] });
  f.selectAll();
  expect(screen.getByRole("button", { name: "Unarchive" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Archive" }));
  await screen.findByText("Updated archive status for 1 record.");
  expect(f.update).toHaveBeenCalledTimes(1);
  expect(f.update).toHaveBeenCalledWith(expect.objectContaining({ id: "one", variables: { archived: true } }));
  expect(f.view().selectedRows.map((row) => row.id)).toEqual(["two"]);
});

test.each(["Archive", "Move to trash"])("%s reserves every generic verb and Clear until it settles", async (first) => {
  let finish!: () => void;
  const pending = new Promise<void>((resolve) => { finish = resolve; });
  const f = await fixture({ rows: [note("one"), note("two", true, true)],
    metadata: { ...resource, roots: { ...resource.roots, deletePreview: "notes_delete" } },
    update: async (id) => { await pending; return { data: { id } }; },
    custom: async () => { await pending; return { trash_record: { ok: true } }; },
  });
  f.selectAll();
  const archive = screen.getByRole<HTMLButtonElement>("button", { name: "Archive" });
  const trash = screen.getByRole<HTMLButtonElement>("button", { name: "Move to trash" });
  const buttons = ["Archive", "Unarchive", "Move to trash", "Restore", "Delete", "Clear"].map((name) =>
    screen.getByRole<HTMLButtonElement>("button", { name }));
  fireEvent.click(first === "Archive" ? archive : trash);
  for (const button of buttons) {
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
  }
  expect(Object.keys(f.view().state.rowSelection).length).toBe(2);
  if (first === "Move to trash") {
    const dialog = await screen.findByRole("alertdialog");
    expect(f.update).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Move to trash" }));
  } else expect(f.custom).not.toHaveBeenCalled();
  await act(async () => { finish(); });
  await screen.findByText(first === "Archive" ? "Updated archive status for 1 record." : "Moved 1 record to the trash.");
  expect(f.update).toHaveBeenCalledTimes(first === "Archive" ? 1 : 0);
  expect(f.custom).toHaveBeenCalledTimes(first === "Move to trash" ? 1 : 0);
  expect(Object.keys(f.view().state.rowSelection).length).toBe(1);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Clear" }).disabled).toBe(false);
});

test.each(["Archive", "Unarchive", "Move to trash", "Restore"])("%s reports and retains selected ids without row snapshots", async (action) => {
  const row = note("one", action === "Unarchive", action === "Restore");
  const f = await fixture({ rows: [row] });
  act(() => f.view().setRowSelection({ one: true, unseen: true }, (id) => id === "one" ? row : undefined));
  fireEvent.click(screen.getByRole("button", { name: action }));
  if (action === "Move to trash") {
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: action }));
  }
  await screen.findByText("1 record was not processed and remains selected.");
  expect(Object.keys(f.view().state.rowSelection)).toEqual(["unseen"]);
  expect(f.update.mock.calls.length + f.custom.mock.calls.length).toBe(1);
});

test.each(["refused", "failed"])("a wholly %s trash run leads with the unsuccessful outcome", async (outcome) => {
  const f = await fixture({ custom: async () => {
    if (outcome === "failed") throw new Error("Connection lost.");
    return { trash_record: { ok: false, message: "Stop active work first." } };
  } });
  f.selectAll();
  fireEvent.click(screen.getByRole("button", { name: "Move to trash" }));
  fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Move to trash" }));
  await screen.findByText(outcome === "refused" ? "2 records were refused." : "Could not move 2 records to the trash.");
  expect(screen.queryByText("Moved 0 records to the trash.")).toBeNull();
  expect(Object.keys(f.view().state.rowSelection).length).toBe(2);
  expect(f.getList).toHaveBeenCalledTimes(1);
});

test.each([
  { ...resource, updateFields: ["title"] },
  { ...resource, roots: { ...resource.roots, update: undefined } },
  { ...resource, fields: flags.map((field) => ({ ...field, updatable: false })) },
])("a resource whose update contract excludes the archive flag offers neither verb", async (metadata) => {
  const f = await fixture({ metadata, rows: [note("one"), note("two", true)] });
  f.selectAll();
  expect(screen.queryByRole("button", { name: /^(Archive|Unarchive)$/ })).toBeNull();
  expect(f.update).not.toHaveBeenCalled();
});

test("trash asks once, calls the shared verb per row, and retains and reports a refused row", async () => {
  const f = await fixture({ rows: [note("one"), note("two"), note("three")], custom: async ({ target_id }) => ({
    trash_record: { ok: target_id === "one", message: "Move refused.",
      validation_errors: target_id === "one" ? null : { __all__: ["Still in use."] } },
  }) });
  f.selectAll();
  const reads = f.getList.mock.calls.length;
  fireEvent.click(screen.getByRole("button", { name: "Move to trash" }));
  const dialog = await screen.findByRole("alertdialog");
  expect(within(dialog).getByText("Move 3 records to the trash?")).toBeTruthy();
  fireEvent.change(within(dialog).getByRole("textbox", { name: "Reason (optional)" }), { target: { value: "  Superseded  " } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Move to trash" }));
  await screen.findByText("Moved 1 record to the trash.");
  expect(screen.getByText("2 refused: Still in use.")).toBeTruthy();
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(f.custom).toHaveBeenCalledTimes(3);
  for (const id of ["one", "two", "three"]) expect(f.custom).toHaveBeenCalledWith(expect.objectContaining({
    payload: { target_type: "notes/note", target_id: id, reason: "Superseded" },
  }));
  expect(f.view().selectedRows.map((row) => row.id)).toEqual(["two", "three"]);
  expect(f.getList).toHaveBeenCalledTimes(reads + 1);
  expect(screen.getAllByRole("dialog", { name: "Moved 1 record to the trash." })).toHaveLength(1);
});

test("Restore handles only trashed rows without a reason prompt", async () => {
  const f = await fixture({ rows: [note("one"), note("two", false, true)] });
  f.selectAll();
  expect(screen.getByRole("button", { name: "Move to trash" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Restore" }));
  await screen.findByText("Restored 1 record from the trash.");
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(f.custom).toHaveBeenCalledTimes(1);
  expect(f.custom).toHaveBeenCalledWith(expect.objectContaining({ payload: { target_type: "notes/note", target_id: "two" } }));
  expect(f.view().selectedRows.map((row) => row.id)).toEqual(["one"]);
});

test("canceling bulk trash leaves every row selected and performs no writes", async () => {
  const f = await fixture();
  f.selectAll();
  fireEvent.click(screen.getByRole("button", { name: "Move to trash" }));
  const dialog = await screen.findByRole("alertdialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Keep 2 records" }));
  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
  expect(f.custom).not.toHaveBeenCalled();
  expect(f.view().selectedRows).toHaveLength(2);
});

test.each([
  { ...resource, resourceType: undefined },
  { ...resource, fields: [flags[0]!] },
])("trash and restore require both the flag and resource type", async (metadata) => {
  const f = await fixture({ metadata, rows: [note("one"), note("two", false, true)] });
  f.selectAll();
  expect(screen.queryByRole("button", { name: /^(Move to trash|Restore)$/ })).toBeNull();
});

test("bulk trash and restore are offered without projected row permissions", async () => {
  const f = await fixture({ metadata: { ...resource, fields: [...flags, permissionField] }, rows: [
    { ...note("one"), permissions: ["write"] }, { ...note("two", false, true), permissions: [] },
    note("missing"), note("missing-removed", false, true),
  ] });
  f.selectAll();
  expect(screen.getByRole("button", { name: "Move to trash" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Restore" })).toBeTruthy();
});

test("unselected archive and trash flags offer neither direction of either action", async () => {
  const f = await fixture({ rows: [
    { id: "missing", title: "Missing flags" },
    { id: "null", title: "Null flags", archived: null, removed: null },
  ] });
  f.selectAll();
  expect(screen.getByText("2 selected")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /^(Archive|Unarchive|Move to trash|Restore)$/ })).toBeNull();
  expect(f.update).not.toHaveBeenCalled();
  expect(f.custom).not.toHaveBeenCalled();
});

test.each(["Archive", "Unarchive", "Move to trash", "Restore"] as const)("bulk %s submits matching rows regardless of projected permissions", async (action) => {
  const archived = action === "Unarchive";
  const removed = action === "Restore";
  const f = await fixture({ metadata: { ...resource, fields: [...flags, permissionField] }, rows: [
    { ...note("allowed", archived, removed), permissions: ["write", "delete"] },
    { ...note("denied", archived, removed), permissions: ["read"] },
    note("missing", archived, removed),
  ] });
  f.selectAll();
  fireEvent.click(screen.getByRole("button", { name: action }));
  if (action === "Move to trash") {
    const dialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("button", { name: action }));
  }
  const archive = action === "Archive" || action === "Unarchive";
  await screen.findByText(archive ? "Updated archive status for 3 records."
    : action === "Restore" ? "Restored 3 records from the trash." : "Moved 3 records to the trash.");
  const write = archive ? f.update : f.custom;
  expect(write).toHaveBeenCalledTimes(3);
  for (const id of ["allowed", "denied", "missing"]) {
    expect(write).toHaveBeenCalledWith(expect.objectContaining(archive
      ? { id } : { payload: expect.objectContaining({ target_id: id }) }));
  }
  expect(f.view().selectedRows).toEqual([]);
});

test.each(["transport", "missing"] as const)("an archive %s failure is reported and stays selected", async (failure) => {
  const f = await fixture({ update: async (id) => {
    if (id === "one") return { data: { id } };
    if (failure === "transport") throw new Error("Archive refused.");
    return { data: {} };
  } });
  f.selectAll();
  fireEvent.click(screen.getByRole("button", { name: "Archive" }));
  await screen.findByText("Updated archive status for 1 record.");
  expect(screen.getByText(/^1 failed:/)).toBeTruthy();
  expect(f.view().selectedRows.map((row) => row.id)).toEqual(["two"]);
});

test.each(["Archive", "Move to trash", "Restore"] as const)("%s caps the operation at 25 rows with five writes in flight and one refresh", async (action) => {
  const releases: (() => void)[] = [];
  const f = await fixture({ rows: Array.from({ length: 27 }, (_, index) => note(`note-${index}`, false, action === "Restore")),
    update: (id) => new Promise((resolve) => releases.push(() => resolve({ data: { id } }))),
    custom: () => new Promise((resolve) => releases.push(() => resolve({
      [action === "Restore" ? "restore_record" : "trash_record"]: { ok: true, message: "Done." },
    }))),
  });
  f.selectAll();
  const reads = f.getList.mock.calls.length;
  fireEvent.click(screen.getByRole("button", { name: action }));
  if (action === "Move to trash") {
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("Move 25 records to the trash?")).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: action }));
  }
  const write = action === "Archive" ? f.update : f.custom;
  for (let batch = 1; batch <= 5; batch++) {
    await waitFor(() => expect(write).toHaveBeenCalledTimes(batch * 5));
    expect(releases).toHaveLength(5);
    await act(async () => { releases.splice(0).forEach((release) => release()); });
  }
  await screen.findByText(action === "Archive" ? "Updated archive status for 25 records."
    : action === "Restore" ? "Restored 25 records from the trash." : "Moved 25 records to the trash.");
  expect(screen.getByText("2 records were not processed and remain selected.")).toBeTruthy();
  expect(write).toHaveBeenCalledTimes(25);
  expect(f.view().selectedRows.map((row) => row.id)).toEqual(["note-25", "note-26"]);
  expect(f.getList).toHaveBeenCalledTimes(reads + 1);
});

test("a source list has no generic actions, even with archivable and trashable metadata", async () => {
  const f = await fixture({ source: true, rows: [note("one"), note("two", true, true)],
    metadata: { ...resource, roots: { ...resource.roots, deletePreview: "notes_delete" } },
  });
  f.selectAll();
  expect(screen.getByText("2 selected")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /^(Archive|Unarchive|Move to trash|Restore|Delete)$/ })).toBeNull();
  expect(f.update).not.toHaveBeenCalled();
});

const preview: RuntimeViewAs = {
  viewAs: { userId: "person" }, currentUser: { id: "person", name: "Person" }, realUser: null,
  viewablePeople: [], enter: () => undefined, exit: () => undefined,
};

test.each([preview, { ...preview, viewAs: null, pending: true }])("view-as blocks all bulk archive and trash writes", async (value) => {
  const f = await fixture({ preview: value, rows: [note("one"), note("two", true, true)] });
  f.selectAll();
  for (const name of ["Archive", "Unarchive", "Move to trash", "Restore"]) {
    const button = screen.getByRole<HTMLButtonElement>("button", { name });
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
  }
  expect(f.update).not.toHaveBeenCalled();
  expect(f.custom).not.toHaveBeenCalled();
});

test("entering view-as while the trash prompt is open blocks its confirmed writes", async () => {
  const f = await fixture();
  f.selectAll();
  fireEvent.click(screen.getByRole("button", { name: "Move to trash" }));
  const dialog = await screen.findByRole("alertdialog");
  f.preview(preview);
  fireEvent.click(within(dialog).getByRole("button", { name: "Move to trash" }));
  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
  expect(f.custom).not.toHaveBeenCalled();
  expect(f.view().selectedRows).toHaveLength(2);
});
