// @vitest-environment happy-dom

import * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import {
  ModelMetadataProvider,
  ResourceQuery,
  schemaFieldMetadataFromDataResources,
} from "@angee/metadata";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "../../feedback";
import type { ListColumn } from "./ListView";

// cmdk scrolls the active option into view; happy-dom has no layout engine.
Element.prototype.scrollIntoView = vi.fn();

interface TestRow extends Record<string, unknown> {
  id: string;
  name: string;
  code: string;
  rank: number;
  note: string | null;
  owner: { id: string; name: string } | null;
}

const harness = vi.hoisted(() => ({
  rows: [
    { id: "row-a", name: "Alpha", code: "A", rank: 1024, note: "first", owner: { id: "own-1", name: "Ada" } },
    { id: "row-b", name: "Beta", code: "B", rank: 2048, note: null, owner: null },
  ] as TestRow[],
  owners: [
    { id: "own-1", name: "Ada" },
    { id: "own-2", name: "Grace" },
  ],
  can: true,
  update: vi.fn(),
}));

vi.mock("@refinedev/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@refinedev/core")>();
  return {
    ...actual,
    useList: (options?: { resource?: string }) => {
      const data = options?.resource === "test_owners" ? harness.owners : harness.rows;
      return {
        result: { data, total: data.length },
        query: { error: null, isFetching: false, refetch: vi.fn() },
      };
    },
    useCan: () => ({ data: { can: harness.can }, isLoading: false, error: null }),
    useInvalidate: () => vi.fn(async () => undefined),
    // Alex's explorer work made the relation widget read through `useOne`
    // (`views/relation/relation-options.ts`); stub it as `ActionFormDialog.test.tsx` does.
    useOne: () => ({
      result: undefined,
      query: { isFetching: false, error: null },
    }),
    useUpdate: () => ({ mutate: vi.fn(), mutateAsync: harness.update }),
  };
});

vi.mock("@angee/refine", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/refine")>();
  return {
    ...actual,
    useAngeeAggregate: () => ({
      aggregate: { key: null, count: harness.rows.length },
      fetching: false,
      error: null,
    }),
    useAngeeFacets: () => ({ facets: {}, fetching: false, error: null }),
    useAngeeGroupBy: () => ({ groups: [], total: undefined, fetching: false, error: null }),
    useOperationDocuments: () => ({}),
  };
});

vi.mock("./useBulkDelete", () => ({
  useBulkDelete: () => ({
    canDelete: false,
    deleteInitiate: vi.fn(),
    isPending: false,
    isPreviewOpen: false,
    onCancel: vi.fn(),
    onConfirm: vi.fn(),
    previewBlockedRecordCount: 0,
    previewOverflowCount: 0,
    previewRecordCount: 0,
    previewState: null,
  }),
}));

import { ListView } from "./ListView";

describe("ListView bulk edit", () => {
  beforeEach(() => {
    harness.can = true;
    harness.update.mockReset();
    harness.update.mockResolvedValue({ data: {} });
  });

  afterEach(() => cleanup());

  test("offers updatable non-rank columns and patches every selected row", async () => {
    renderList();
    await openEditMenu();
    expect(await screen.findByRole("menuitem", { name: "Set Name" })).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: "Set Code" })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: "Set Rank" })).toBeNull();
    const dialog = await chooseField("Set Name");

    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "Renamed" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Set Name" }));

    await waitFor(() => expect(harness.update).toHaveBeenCalledTimes(2));
    expect(harness.update).toHaveBeenCalledWith({ id: "row-a", values: { name: "Renamed" } });
    expect(harness.update).toHaveBeenCalledWith({ id: "row-b", values: { name: "Renamed" } });
    expect(await screen.findByText("2 updated")).toBeTruthy();
  });

  test("edits a relation column through its relation", async () => {
    renderList();
    await openEditMenu();
    const dialog = await chooseField("Set Owner");

    fireEvent.click(within(dialog).getByRole("button", { name: "Owner" }));
    fireEvent.click(await screen.findByText("Grace"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Set Owner" }));

    await waitFor(() => expect(harness.update).toHaveBeenCalledTimes(2));
    expect(harness.update).toHaveBeenCalledWith({ id: "row-a", values: { owner: "own-2" } });
    expect(harness.update).toHaveBeenCalledWith({ id: "row-b", values: { owner: "own-2" } });
  });

  test("narrows the selection to the rejected rows so a retry patches only those", async () => {
    harness.update.mockImplementation(async ({ id }: { id: string }) => {
      if (id === "row-b") throw new Error("row-b is locked");
      return { data: {} };
    });
    renderList();
    await openEditMenu();
    const dialog = await chooseField("Set Name");

    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "Renamed" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Set Name" }));

    expect(await within(dialog).findByText("1 of 2 updated. row-b is locked")).toBeTruthy();
    expect(await screen.findByText("1 selected")).toBeTruthy();

    harness.update.mockReset();
    harness.update.mockResolvedValue({ data: {} });
    fireEvent.click(within(dialog).getByRole("button", { name: "Set Name" }));

    await waitFor(() => expect(harness.update).toHaveBeenCalledTimes(1));
    expect(harness.update).toHaveBeenCalledWith({ id: "row-b", values: { name: "Renamed" } });
  });

  test("clears an optional scalar or relation left empty instead of writing an empty string", async () => {
    renderList();
    await openEditMenu();
    let dialog = await chooseField("Set Note");
    fireEvent.click(within(dialog).getByRole("button", { name: "Set Note" }));
    await waitFor(() => expect(harness.update).toHaveBeenCalledTimes(2));
    expect(harness.update).toHaveBeenCalledWith({ id: "row-a", values: { note: null } });

    await waitFor(() => expect(screen.queryByRole("button", { name: "Set Note" })).toBeNull());
    harness.update.mockClear();
    await openEditMenu();
    dialog = await chooseField("Set Owner");
    fireEvent.click(within(dialog).getByRole("button", { name: "Set Owner" }));
    await waitFor(() => expect(harness.update).toHaveBeenCalledTimes(2));
    expect(harness.update).toHaveBeenCalledWith({ id: "row-b", values: { owner: null } });
  });

  test("renders no Edit trigger when edit access is denied", async () => {
    harness.can = false;
    renderList();
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select all rows on this page" }));

    expect(await screen.findByText("2 selected")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  });

  test("lets page-authored bulk actions replace the menu", async () => {
    renderList({ bulkActions: () => <button type="button">Archive</button> });
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select all rows on this page" }));

    expect(await screen.findByRole("button", { name: "Archive" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  });
});

async function openEditMenu(): Promise<void> {
  if (!screen.queryByText("2 selected")) {
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select all rows on this page" }));
  }
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
}

/** Choose a field from the Edit menu and return its form dialog, found by its submit button. */
async function chooseField(label: string): Promise<HTMLElement> {
  fireEvent.click(await screen.findByRole("menuitem", { name: label }));
  const submit = await screen.findByRole("button", { name: label });
  const dialog = submit.closest<HTMLElement>("[role='dialog']");
  if (!dialog) throw new Error(`Expected the ${label} form dialog.`);
  return dialog;
}

const COLUMNS: readonly ListColumn<TestRow>[] = [
  { field: "name", header: "Name" },
  { field: "code", header: "Code" },
  { field: "rank", header: "Rank" },
  { field: "note", header: "Note" },
  { field: "owner", header: "Owner" },
];

function renderList(
  props: { bulkActions?: (ids: ReadonlySet<string>, clear: () => void) => React.ReactNode } = {},
): ReturnType<typeof render> {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ModelMetadataProvider metadata={TEST_METADATA}>
        <ModalsHost>
          <ToastProvider>
            <ListView<TestRow> resource="test.Row" scope="local" columns={COLUMNS} {...props} />
          </ToastProvider>
        </ModalsHost>
      </ModelMetadataProvider>
    </QueryClientProvider>,
  );
}

const ROW_QUERY = ResourceQuery.forRows({
  fields: {
    id: { scalar: "ID" },
    name: { scalar: "String" },
    code: { scalar: "String" },
    rank: { scalar: "Float" },
    note: { scalar: "String" },
    owner: { kind: "relation", identityPath: "owner.id", labelPath: "owner.name" },
  },
}).contract;
ROW_QUERY.fields.owner!.relation = { model: "test.Owner", identityPath: "owner.id", labelPath: "owner.name" };

const TEST_METADATA = schemaFieldMetadataFromDataResources([
  {
    schemaName: "console",
    modelLabel: "test.Row",
    appLabel: "test",
    modelName: "row",
    roots: { list: "test_rows", aggregate: "test_rows_aggregate", update: "update_test_rows_by_pk" },
    typeNames: { node: "TestRowType", filter: "TestRowBoolExp", order: "TestRowOrderBy" },
    recordRepresentation: "name",
    capabilities: ["list", "aggregate", "update"],
    fields: [
      field("id", "ID"),
      field("name", "String"),
      { ...field("code", "String"), updatable: false },
      field("rank", "Float"),
      { ...field("note", "String"), nullable: true },
      {
        ...field("owner", "ID"),
        kind: "relation" as const,
        scalar: null,
        relationObject: true,
        relationModelLabel: "test.Owner",
        nullable: true,
      },
    ],
    query: ROW_QUERY,
    aggregateFields: ["id", "name"],
  },
  {
    schemaName: "console",
    modelLabel: "test.Owner",
    appLabel: "test",
    modelName: "owner",
    roots: { list: "test_owners" },
    typeNames: { node: "TestOwnerType", filter: "TestOwnerBoolExp", order: "TestOwnerOrderBy" },
    recordRepresentation: "name",
    capabilities: ["list"],
    fields: [field("id", "ID"), field("name", "String")],
    query: ResourceQuery.forRows({ fields: { id: { scalar: "ID" }, name: { scalar: "String" } } }).contract,
    aggregateFields: [],
  },
]);

function field(name: string, scalar: string) {
  return {
    name,
    kind: "scalar" as const,
    scalar,
    readable: true,
    aggregatable: true,
    nullable: false,
    creatable: true,
    updatable: true,
    requiredOnCreate: false,
  };
}
