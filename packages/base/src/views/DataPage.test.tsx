// @vitest-environment happy-dom

import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  useRouterState,
} from "@tanstack/react-router";
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";
import { afterEach, beforeAll, describe, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "../feedback";
import { parseFlatSearch, stringifyFlatSearch } from "../createApp";
import { DataPage } from "./DataPage";
import { Form } from "./Form";
import type { FormField } from "./FormView";
import {
  List,
  type ListComponent,
} from "./List";
import {
  ListView,
  type ListColumn,
  type ListViewProps,
} from "./ListView";
import { GroupListView } from "./GroupListView";
import {
  Action,
  Column,
  Field,
  Group,
} from "./page";
import type {
  AggregateBucket,
  GroupByDimension,
  ResourceTypeName,
  UseAggregateOptions,
  Row,
  UseGroupByOptions,
  UseResourceListOptions,
  UseResourceListResult,
} from "@angee/sdk";

const sdkMocks = vi.hoisted(() => ({
  rows: [
    {
      id: "note-1",
      title: "First",
      status: "ACTIVE",
      priority: "High",
      wordCount: 10,
      updatedAt: "2026-01-03T10:00:00.000Z",
    },
    {
      id: "note-2",
      title: "Second",
      status: "ACTIVE",
      priority: "Low",
      wordCount: 20,
      updatedAt: "2026-02-03T10:00:00.000Z",
    },
    {
      id: "note-3",
      title: "Third",
      status: "DRAFT",
      priority: "Medium",
      wordCount: 5,
      updatedAt: "2026-03-03T10:00:00.000Z",
    },
    {
      id: "note-4",
      title: "Fourth",
      status: "ARCHIVED",
      priority: "Low",
      wordCount: 8,
      updatedAt: "2026-04-03T10:00:00.000Z",
    },
  ] satisfies Row[],
  aggregateCalls: [] as Array<UseAggregateOptions<ResourceTypeName>>,
  groupByCalls: [] as Array<UseGroupByOptions<ResourceTypeName>>,
  mutate: vi.fn(async ({ data }: { data: Row }) => data),
}));

vi.mock("@angee/sdk", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/sdk")>();
  const ReactRuntime = await import("react");
  function filteredRows(
    rows: readonly Row[],
    filter: UseResourceListOptions<ResourceTypeName>["filter"],
  ): readonly Row[] {
    if (!filter) return rows;
    return rows.filter((row) =>
      Object.entries(filter as Record<string, unknown>).every(
        ([field, lookup]) => matchesLookup(readPath(row, field), lookup),
      ),
    );
  }
  function matchesLookup(value: unknown, lookup: unknown): boolean {
    if (!lookup || typeof lookup !== "object" || Array.isArray(lookup)) {
      return value === lookup;
    }
    const record = lookup as Record<string, unknown>;
    if ("exact" in record) return value === record.exact;
    if (Array.isArray(record.inList)) return record.inList.includes(value);
    if (typeof record.iContains === "string") {
      return String(value ?? "")
        .toLowerCase()
        .includes(record.iContains.toLowerCase());
    }
    return true;
  }
  function readPath(row: Row, path: string): unknown {
    let current: unknown = row;
    for (const key of path.split(".")) {
      if (current == null || typeof current !== "object") return undefined;
      current = (current as Record<string, unknown>)[key];
    }
    return current;
  }
  function groupBuckets(
    rows: readonly Row[],
    dimensions: readonly GroupByDimension[],
    baseFilter: UseGroupByOptions<ResourceTypeName>["filter"],
    measures: UseGroupByOptions<ResourceTypeName>["measures"],
  ): readonly AggregateBucket[] {
    const buckets = new Map<string, AggregateBucket>();
    for (const row of rows) {
      const key: Record<string, unknown> = {};
      const filter = { ...(baseFilter as Record<string, unknown> | undefined) };
      for (const dimension of dimensions) {
        const keyField = dimension.key ?? dimension.field;
        const sourceField = sourceFieldForAggregateKey(keyField);
        const value = readPath(row, sourceField);
        key[keyField] = value;
        filter[sourceField] = { exact: value };
      }
      const bucketKey = JSON.stringify(key);
      const current = buckets.get(bucketKey);
      if (current) {
        buckets.set(
          bucketKey,
          applyMeasures({ ...current, count: current.count + 1 }, row, measures),
        );
      } else {
        buckets.set(
          bucketKey,
          applyMeasures({ key, count: 1, filter }, row, measures),
        );
      }
    }
    return [...buckets.values()];
  }
  function aggregateBucket(
    rows: readonly Row[],
    measures: UseAggregateOptions<ResourceTypeName>["measures"],
  ): AggregateBucket {
    return rows.reduce<AggregateBucket>(
      (bucket, row) => applyMeasures(bucket, row, measures),
      { key: null, count: rows.length },
    );
  }
  function applyMeasures(
    bucket: AggregateBucket,
    row: Row,
    measures: UseGroupByOptions<ResourceTypeName>["measures"],
  ): AggregateBucket {
    let next = bucket;
    for (const measure of measures ?? []) {
      if (measure.op !== "sum") continue;
      const value = numberValue(readPath(row, measure.field));
      const current = numberValue(next.sum?.[measure.field]);
      next = {
        ...next,
        sum: {
          ...next.sum,
          [measure.field]: current + value,
        },
      };
    }
    return next;
  }
  function numberValue(value: unknown): number {
    return typeof value === "number" && Number.isFinite(value) ? value : 0;
  }
  function sourceFieldForAggregateKey(key: string): string {
    const field = key.replace(/(?:Day|Week|Month|Quarter|Year)$/, "");
    if (!field.includes("_")) {
      return `${field.charAt(0).toLowerCase()}${field.slice(1)}`;
    }
    return field
      .toLowerCase()
      .replace(/_([a-z])/g, (_, letter: string) => letter.toUpperCase());
  }
  return {
    ...actual,
    useResourceList: (
      _model: string,
      options: UseResourceListOptions<ResourceTypeName>,
    ): UseResourceListResult => {
      const pageSize = options.pageSize ?? 50;
      const active = options.enabled !== false;
      const matchingRows = active
        ? filteredRows(sdkMocks.rows, options.filter)
        : [];
      const pageCount = Math.max(
        1,
        Math.ceil(matchingRows.length / pageSize),
      );
      const requestedPage = Math.min(
        pageCount,
        Math.max(1, options.page ?? options.initialPage ?? 1),
      );
      const [page, setPageState] = ReactRuntime.useState(requestedPage);
      ReactRuntime.useEffect(() => {
        setPageState(requestedPage);
      }, [requestedPage]);
      const visiblePage = Math.min(page, pageCount);
      const rows = matchingRows.slice(
        (visiblePage - 1) * pageSize,
        visiblePage * pageSize,
      );
      const setPage = (next: number) => {
        setPageState(Math.min(pageCount, Math.max(1, Math.floor(next))));
      };
      return {
        rows,
        total: active ? matchingRows.length : undefined,
        pageCount,
        page: visiblePage,
        pageSize,
        pageInfo: undefined,
        hasNext: visiblePage < pageCount,
        hasPrev: visiblePage > 1,
        setPage,
        firstPage: () => setPage(1),
        nextPage: () => setPage(visiblePage + 1),
        prevPage: () => setPage(visiblePage - 1),
        lastPage: () => setPage(pageCount),
        fetching: false,
        error: null,
        refetch: vi.fn(),
      };
    },
    useResourceRecord: (_model: string, id: string | null) => ({
      record: sdkMocks.rows.find((row) => row.id === id) ?? null,
      fetching: false,
      error: null,
      refetch: vi.fn(),
    }),
    useResourceGroupBy: (
      _model: string,
      options: UseGroupByOptions<ResourceTypeName>,
    ) => {
      sdkMocks.groupByCalls.push(options);
      if (options.enabled === false || options.dimensions.length === 0) {
        return {
          count: 0,
          totalCount: 0,
          buckets: [],
          fetching: false,
          error: null,
        };
      }
      const buckets = groupBuckets(
        filteredRows(sdkMocks.rows, options.filter),
        options.dimensions,
        options.filter,
        options.measures,
      );
      const pageSize = options.pageSize ?? buckets.length;
      const page = Math.max(1, options.page ?? 1);
      const visibleBuckets = buckets.slice(
        (page - 1) * pageSize,
        page * pageSize,
      );
      return {
        count: visibleBuckets.reduce((total, bucket) => total + bucket.count, 0),
        totalCount: buckets.length,
        buckets: visibleBuckets,
        fetching: false,
        error: null,
      };
    },
    useResourceAggregate: (
      _model: string,
      options: UseAggregateOptions<ResourceTypeName>,
    ) => {
      sdkMocks.aggregateCalls.push(options);
      const active = options.enabled !== false;
      return {
        aggregate: active
          ? aggregateBucket(filteredRows(sdkMocks.rows, options.filter), options.measures)
          : null,
        fetching: false,
        error: null,
      };
    },
    useResourceMutation: () => [
      sdkMocks.mutate,
      { fetching: false, error: null },
    ],
    useWidget: () => undefined,
  };
});

const columns = [
  { field: "title", header: "Title" },
] satisfies readonly ListColumn[];

const formFields = [
  { name: "title", label: "Title", title: true },
] satisfies readonly FormField[];

describe("DataPage", () => {
  beforeAll(() => {
    Element.prototype.getAnimations ??= () => [];
  });

  afterEach(async () => {
    await act(async () => {
      cleanup();
      await nextTask();
    });
  });

  test("renders the lean ListView as a flat list without group controls", async () => {
    render(
      <TestUrlState searchParams="?view=board&group=status">
        {/* Lean ListView ignores grouping/board even when the URL carries them,
            and its type no longer accepts defaultGroup. */}
        <ListView model="notes.Note" columns={columns} />
      </TestUrlState>,
    );

    expect(await screen.findByText("First")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Board view" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Remove group" })).toBeNull();
    const visibleFieldsButton = screen.getByRole("button", {
      name: "Visible fields",
    });
    expect(visibleFieldsButton.closest("thead")).not.toBeNull();
    expect(
      visibleFieldsButton.closest('section[aria-label="Data controls"]'),
    ).toBeNull();

    fireEvent.click(
      screen.getByRole("button", { name: "Filter and favorites" }),
    );

    expect(screen.queryByText("Group by")).toBeNull();
  });

  test("parses List child columns and forwards props into the list renderer", async () => {
    const captured: { current: ListViewProps<Row> | null } = { current: null };
    const CapturingList: ListComponent<Row> = (props) => {
      captured.current = props;
      return <div data-testid="captured-list" />;
    };
    const rowHref = (row: Row) =>
      typeof row.id === "string" ? `/notes/${row.id}` : "/notes";

    render(
      <TestUrlState>
        <DataPage
          model="notes.Note"
          formFields={formFields}
          list={CapturingList}
          rowHref={rowHref}
        >
          <List
            createLabel="Add note"
            emptyMessage="No matching notes."
            filters={[{ id: "active", label: "Active", filter: {} }]}
          >
            <Column field="title" header="Title" />
            <Column
              field="wordCount"
              header="Words"
              align="right"
              aggregate="sum"
            />
          </List>
        </DataPage>
      </TestUrlState>,
    );

    expect(await screen.findByTestId("captured-list")).toBeTruthy();
    expect(captured.current?.model).toBe("notes.Note");
    expect(captured.current?.columns).toEqual([
      { field: "title", header: "Title" },
      {
        field: "wordCount",
        header: "Words",
        align: "right",
        aggregate: "sum",
      },
    ]);
    expect(captured.current?.filters).toEqual([
      { id: "active", label: "Active", filter: {} },
    ]);
    expect(captured.current?.createLabel).toBe("Add note");
    expect(captured.current?.emptyMessage).toBe("No matching notes.");
    expect(captured.current?.rowHref).toBe(rowHref);
  });

  test("parses Form child fields and groups into DataPage form descriptors", async () => {
    render(
      <TestUrlState>
        <DataPage
          model="notes.Note"
          columns={columns}
          recordId="note-1"
        >
          <Form>
            <Field name="title" label="Title" title />
            <Group label="Details" columns={2}>
              <Field name="priority" label="Priority" readOnly />
            </Group>
          </Form>
        </DataPage>
      </TestUrlState>,
    );

    await waitFor(() =>
      expect((screen.getByLabelText("Title") as HTMLInputElement).value).toBe(
        "First",
      ),
    );
    expect(screen.getByText("Details")).toBeTruthy();
    expect(screen.getByText("High")).toBeTruthy();
  });

  test.each([
    {
      name: "formFields plus Form child",
      element: (
        <DataPage
          model="notes.Note"
          columns={columns}
          formFields={formFields}
        >
          <Form>
            <Field name="title" />
          </Form>
        </DataPage>
      ),
      message: /DataPage and its Form child both declare "formFields"/,
    },
    {
      name: "formGroups plus Form child",
      element: (
        <DataPage
          model="notes.Note"
          columns={columns}
          formGroups={[]}
        >
          <Form>
            <Field name="title" />
          </Form>
        </DataPage>
      ),
      message: /DataPage and its Form child both declare "formGroups"/,
    },
    {
      name: "columns plus List child",
      element: (
        <DataPage
          model="notes.Note"
          columns={columns}
          formFields={formFields}
        >
          <List>
            <Column field="title" />
          </List>
        </DataPage>
      ),
      message: /DataPage and its List child both declare "columns"/,
    },
    {
      name: "duplicate List children",
      element: (
        <DataPage model="notes.Note" formFields={formFields}>
          <List>
            <Column field="title" />
          </List>
          <List>
            <Column field="status" />
          </List>
        </DataPage>
      ),
      message: /only one List child/,
    },
    {
      name: "duplicate Form children",
      element: (
        <DataPage model="notes.Note" columns={columns}>
          <Form>
            <Field name="title" />
          </Form>
          <Form>
            <Field name="status" />
          </Form>
        </DataPage>
      ),
      message: /only one Form child/,
    },
    {
      name: "List model mismatch",
      element: (
        <DataPage model="notes.Note" formFields={formFields}>
          <List model="tasks.Task">
            <Column field="title" />
          </List>
        </DataPage>
      ),
      message: /does not match DataPage model/,
    },
    {
      name: "Form model mismatch",
      element: (
        <DataPage model="notes.Note" columns={columns}>
          <Form model="tasks.Task">
            <Field name="title" />
          </Form>
        </DataPage>
      ),
      message: /does not match DataPage model/,
    },
    {
      name: "unknown element child",
      element: (
        <DataPage
          model="notes.Note"
          columns={columns}
          formFields={formFields}
        >
          <Column field="title" />
        </DataPage>
      ),
      message: /wrapper components hide the marker/,
    },
    {
      name: "unknown text child",
      element: (
        <DataPage
          model="notes.Note"
          columns={columns}
          formFields={formFields}
        >
          text
        </DataPage>
      ),
      message: /DataPage child text "text"/,
    },
    {
      name: "empty nested List",
      element: (
        <DataPage model="notes.Note" formFields={formFields}>
          <List />
        </DataPage>
      ),
      message: /requires at least one Column child/,
    },
    {
      name: "forwarded prop overlap",
      element: (
        <DataPage
          model="notes.Note"
          formFields={formFields}
          order={{ title: "ASC" }}
        >
          <List order={{ title: "DESC" }}>
            <Column field="title" />
          </List>
        </DataPage>
      ),
      message: /DataPage and its List child both declare "order"/,
    },
    {
      name: "DataPage-owned List wiring",
      element: (
        <DataPage model="notes.Note" formFields={formFields}>
          <List onCreate={() => undefined}>
            <Column field="title" />
          </List>
        </DataPage>
      ),
      message: /DataPage owns List child "onCreate" wiring/,
    },
    {
      name: "DataPage-owned Form wiring",
      element: (
        <DataPage model="notes.Note" columns={columns}>
          <Form id="note-1">
            <Field name="title" />
          </Form>
        </DataPage>
      ),
      message: /DataPage owns Form child "id" wiring/,
    },
  ])("rejects invalid DataPage declarations: $name", ({ element, message }) => {
    expect(() => render(element)).toThrow(message);
  });

  test.each([
    {
      name: "List without model",
      element: (
        <List>
          <Column field="title" />
        </List>
      ),
      message: /List requires a model/,
    },
    {
      name: "Form without model",
      element: (
        <Form>
          <Field name="title" />
        </Form>
      ),
      message: /Form requires a model/,
    },
    {
      name: "standalone empty List",
      element: <List model="notes.Note" />,
      message: /requires at least one Column child/,
    },
  ])("rejects invalid standalone view declarations: $name", ({ element, message }) => {
    expect(() => render(element)).toThrow(message);
  });

  test("renders standalone List from Column children", async () => {
    render(
      <TestUrlState>
        <List model="notes.Note">
          <Column field="title" header="Title" />
          <Column field="status" header="Status" />
        </List>
      </TestUrlState>,
    );

    expect(await screen.findByText("First")).toBeTruthy();
    expect(screen.getByText("Status")).toBeTruthy();
  });

  test("renders record navigation and reuses the view switcher in record chrome", async () => {
    const onSelect = vi.fn();
    const onClose = vi.fn();

    function Harness(): ReactElement {
      const [recordId, setRecordId] = useState<string | null>("note-2");
      return (
        <TestUrlState>
          <DataPage
            model="notes.Note"
            columns={columns}
            formFields={formFields}
            recordId={recordId}
            placement="inline"
            pageSize={2}
            recordSmartButtons={[
              { id: "linked", icon: "plus", count: 7, label: "Linked notes" },
              { id: "comments", icon: "comments", count: 12, label: "Comments" },
            ]}
            onSelect={(id) => {
              onSelect(id);
              setRecordId(id);
            }}
            onClose={onClose}
          />
        </TestUrlState>
      );
    }

    render(<Harness />);

    const pager = await screen.findByRole("navigation", {
      name: "Record navigation",
    });
    expect(pager.textContent?.replace(/\s+/g, " ").trim()).toContain(
      "2 / 4",
    );
    expect(
      screen.getByRole("button", { name: "7 Linked notes" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "12 Comments" }),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    expect(await screen.findByRole("menuitem", { name: "Delete" })).toBeTruthy();

    fireEvent.click(
      within(pager).getByRole("button", { name: "Next record" }),
    );
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith("note-3"));
    await waitFor(() =>
      expect(
        screen
          .getByRole("navigation", { name: "Record navigation" })
          .textContent?.replace(/\s+/g, " ")
          .trim(),
      ).toContain("3 / 4"),
    );

    const switcher = screen.getByRole("group", {
      name: "Record view switcher",
    });
    const boardButton = within(switcher).getByRole("button", {
      name: "Board view",
    });
    fireEvent.click(boardButton);
    expect(onClose).toHaveBeenCalledTimes(1);
    await waitFor(() =>
      expect(boardButton.getAttribute("aria-pressed")).toBe("true"),
    );
  });

  test("folds record actions into the Actions menu", async () => {
    render(
      <TestUrlState>
        <DataPage
          model="notes.Note"
          columns={columns}
          recordId="note-2"
          placement="inline"
        >
          <Form>
            <Field name="title" label="Title" title />
            <Action id="archive" label="Archive" set={{ status: "ARCHIVED" }} />
          </Form>
        </DataPage>
      </TestUrlState>,
    );

    await screen.findByLabelText("Title");
    expect(screen.getAllByRole("button", { name: "Actions" })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Archive" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    expect(await screen.findByRole("menuitem", { name: "Delete" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "Archive" })).toBeTruthy();
  });

  test("reads board state from Router search and writes view changes", async () => {
    const onSelect = vi.fn();
    const onUrlUpdate = vi.fn();
    const boardColumns = [
      { field: "title", header: "Title" },
      {
        field: "status",
        header: "Status",
        tone: {
          ACTIVE: "success",
          DRAFT: "warning",
          ARCHIVED: "neutral",
        },
      },
      { field: "priority", header: "Priority" },
    ] satisfies readonly ListColumn[];

    render(
      <TestUrlState
        searchParams="?view=board&group=status"
        onUrlUpdate={onUrlUpdate}
      >
        <DataPage
          model="notes.Note"
          columns={boardColumns}
          formFields={formFields}
          list={GroupListView}
          onSelect={onSelect}
          rowHref={(row) => row.id === "note-1" ? "/notes/note-1" : ""}
        />
      </TestUrlState>,
    );

    const activeLane = await screen.findByRole("region", { name: "Active" });
    const draftLane = await screen.findByRole("region", { name: "Draft" });
    const archivedLane = await screen.findByRole("region", {
      name: "Archived",
    });
    expect(
      screen.getAllByRole("heading", { level: 3 }).map((heading) =>
        heading.textContent,
      ),
    ).toEqual(["Active", "Draft", "Archived"]);
    expect(within(activeLane).getByRole("heading", { name: "Active" }))
      .toBeTruthy();
    expect(within(activeLane).getByText("2")).toBeTruthy();
    expect(within(draftLane).getByRole("heading", { name: "Draft" }))
      .toBeTruthy();
    expect(within(draftLane).getByText("1")).toBeTruthy();
    expect(within(archivedLane).getByRole("heading", { name: "Archived" }))
      .toBeTruthy();
    expect(within(archivedLane).getByText("1")).toBeTruthy();

    const linkedCard = within(activeLane).getByRole("link", { name: /First/ });
    expect(within(linkedCard).queryByText("Status")).toBeNull();
    expect(within(linkedCard).queryByText("Active")).toBeNull();
    expect(within(linkedCard).getByText("Priority")).toBeTruthy();
    expect(within(linkedCard).getByText("High")).toBeTruthy();

    const clickableCard = within(activeLane).getByRole("button", {
      name: /Second/,
    });
    fireEvent.click(clickableCard);
    expect(onSelect).toHaveBeenCalledWith("note-2");

    fireEvent.click(screen.getByRole("button", { name: "List view" }));
    await waitFor(() => {
      const latest = onUrlUpdate.mock.calls.at(-1)?.[0];
      expect(latest?.searchParams.get("view")).toBeNull();
      expect(latest?.searchParams.get("group")).toBe("status");
    });
  });

  test("seeds different default groups for list and board views", async () => {
    const onUrlUpdate = vi.fn();
    const boardColumns = [
      { field: "title", header: "Title" },
      {
        field: "status",
        header: "Status",
        tone: {
          ACTIVE: "success",
          DRAFT: "warning",
          ARCHIVED: "neutral",
        },
      },
      { field: "updatedAt", header: "Updated At" },
    ] satisfies readonly ListColumn[];

    render(
      <TestUrlState onUrlUpdate={onUrlUpdate}>
        <DataPage
          model="notes.Note"
          columns={boardColumns}
          formFields={formFields}
          list={GroupListView}
          defaultGroups={{
            list: { field: "updatedAt", granularity: "month" },
            board: { field: "status" },
          }}
        />
      </TestUrlState>,
    );

    await screen.findByText("Updated · Month");
    await waitFor(() => {
      const latest = onUrlUpdate.mock.calls.at(-1)?.[0];
      expect(latest?.searchParams.get("group")).toBe("updatedAt:month");
      expect(latest?.searchParams.get("view")).toBeNull();
    });

    fireEvent.click(screen.getByRole("button", { name: "Board view" }));

    await screen.findByRole("region", { name: "Active" });
    await waitFor(() => {
      const latest = onUrlUpdate.mock.calls.at(-1)?.[0];
      expect(latest?.searchParams.get("group")).toBe("status");
      expect(latest?.searchParams.get("view")).toBe("board");
    });

    fireEvent.click(screen.getByRole("button", { name: "List view" }));

    await screen.findByText("Updated · Month");
    await waitFor(() => {
      const latest = onUrlUpdate.mock.calls.at(-1)?.[0];
      expect(latest?.searchParams.get("group")).toBe("updatedAt:month");
      expect(latest?.searchParams.get("view")).toBeNull();
    });
  });

  test("lets the seeded default group be cleared", async () => {
    render(
      <TestUrlState>
        <DataPage
          model="notes.Note"
          columns={[...columns, { field: "updatedAt", header: "Updated At" }]}
          formFields={formFields}
          list={GroupListView}
          defaultGroup={{ field: "updatedAt", granularity: "day" }}
        />
      </TestUrlState>,
    );

    const removeGroup = await screen.findByRole("button", {
      name: "Remove group",
    });
    fireEvent.click(removeGroup);

    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Remove group" }),
      ).toBeNull(),
    );
  });

  test("renders grouped lists folded and expands group items lazily", async () => {
    const onSelect = vi.fn();

    render(
      <TestUrlState searchParams="?group=status&pageSize=2">
        <DataPage
          model="notes.Note"
          columns={columns}
          formFields={formFields}
          list={GroupListView}
          onSelect={onSelect}
        />
      </TestUrlState>,
    );

    await screen.findByRole("button", { name: "Groups 1-2 / 3 groups" });
    const activeGroup = await screen.findByRole("button", { name: /Active/ });
    expect(activeGroup.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("button", { name: "Open First" })).toBeNull();

    fireEvent.click(activeGroup);

    await waitFor(() =>
      expect(activeGroup.getAttribute("aria-expanded")).toBe("true"),
    );
    expect(await screen.findByRole("button", { name: "Open First" }))
      .toBeTruthy();
    expect(screen.getByText("1-2 / 2")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Open First" }));
    expect(onSelect).toHaveBeenCalledWith("note-1");
  });

  test("renders grouped list aggregate measures and a grand total footer", async () => {
    sdkMocks.groupByCalls.length = 0;
    sdkMocks.aggregateCalls.length = 0;
    const measuredColumns = [
      { field: "title", header: "Title" },
      { field: "status", header: "Status" },
      {
        field: "wordCount",
        header: "Word Count",
        align: "right",
        aggregate: "sum",
      },
    ] satisfies readonly ListColumn[];

    render(
      <TestUrlState searchParams="?group=status&pageSize=10">
        <DataPage
          model="notes.Note"
          columns={measuredColumns}
          formFields={formFields}
          list={GroupListView}
        />
      </TestUrlState>,
    );

    const activeGroup = await screen.findByRole("button", { name: /Active/ });
    expect(within(activeGroup).getByText("30 words")).toBeTruthy();
    expect(
      (await screen.findByLabelText("Total Word Count: 43 words")).textContent,
    ).toBe("43 words");
    expect(
      sdkMocks.groupByCalls.some((call) =>
        call.measures?.some(
          (measure) =>
            measure.op === "sum" && measure.field === "wordCount",
        ),
      ),
    ).toBe(true);
    expect(
      sdkMocks.aggregateCalls.some((call) =>
        call.measures?.some(
          (measure) =>
            measure.op === "sum" && measure.field === "wordCount",
        ),
      ),
    ).toBe(true);
  });

  test("paginates through the URL-owned data view state", async () => {
    const onUrlUpdate = vi.fn();
    render(
      <TestUrlState
        searchParams="?group=updatedAt:day"
        onUrlUpdate={onUrlUpdate}
      >
        <DataPage
          model="notes.Note"
          columns={[...columns, { field: "updatedAt", header: "Updated At" }]}
          formFields={formFields}
          list={GroupListView}
          pageSize={2}
          defaultGroup={{ field: "updatedAt", granularity: "day" }}
        />
      </TestUrlState>,
    );

    await screen.findByRole("button", { name: "Groups 1-2 / 4 groups" });
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Groups 3-4 / 4 groups" }),
      ).toBeTruthy(),
    );
    await waitFor(() => {
      const latest = onUrlUpdate.mock.calls.at(-1)?.[0];
      expect(latest?.searchParams.get("page")).toBe("2");
    });
  });

  test("selects page size from the pager range popover", async () => {
    const onUrlUpdate = vi.fn();
    render(
      <TestUrlState onUrlUpdate={onUrlUpdate}>
        <DataPage
          model="notes.Note"
          columns={columns}
          formFields={formFields}
          pageSize={2}
        />
      </TestUrlState>,
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "Records 1-2 / 4" }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "10" }));

    await waitFor(() => {
      const latest = onUrlUpdate.mock.calls.at(-1)?.[0];
      expect(latest?.searchParams.get("pageSize")).toBe("10");
    });
  });

  test("adds a custom filter from the toolbar editor", async () => {
    const onUrlUpdate = vi.fn();
    render(
      <TestUrlState onUrlUpdate={onUrlUpdate}>
        <DataPage
          model="notes.Note"
          columns={columns}
          formFields={formFields}
        />
      </TestUrlState>,
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "Filter and favorites" }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Add custom filter" }),
    );
    fireEvent.change(await screen.findByRole("textbox", { name: "Filter value" }), {
      target: { value: "Fir" },
    });
    fireEvent.click(await screen.findByRole("button", { name: "Add" }));

    await waitFor(() => {
      const latest = onUrlUpdate.mock.calls.at(-1)?.[0];
      expect(JSON.parse(latest?.searchParams.get("filter") ?? "{}")).toEqual({
        title: { contains: "Fir" },
      });
    });
    expect(await screen.findByText("Title contains Fir")).toBeTruthy();
  });

  test("saves and reapplies the current data-view search", async () => {
    const onUrlUpdate = vi.fn();
    render(
      <TestUrlState onUrlUpdate={onUrlUpdate}>
        <DataPage
          model="notes.Note"
          columns={columns}
          formFields={formFields}
          pageSize={2}
        />
      </TestUrlState>,
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "Filter and favorites" }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Save current search" }),
    );
    fireEvent.change(await screen.findByRole("textbox", { name: "Favorite name" }), {
      target: { value: "Two per page" },
    });
    fireEvent.click(await screen.findByRole("button", { name: "Save" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Filter and favorites" }),
    );
    expect(await screen.findByRole("button", { name: "Two per page" }))
      .toBeTruthy();
    fireEvent.keyDown(document, { key: "Escape" });

    fireEvent.click(
      await screen.findByRole("button", { name: "Records 1-2 / 4" }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "10" }));
    await waitFor(() => {
      const latest = onUrlUpdate.mock.calls.at(-1)?.[0];
      expect(latest?.searchParams.get("pageSize")).toBe("10");
    });

    fireEvent.click(
      await screen.findByRole("button", { name: "Filter and favorites" }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Two per page" }));
    await waitFor(() => {
      const latest = onUrlUpdate.mock.calls.at(-1)?.[0];
      expect(latest?.searchParams.get("pageSize")).toBe("2");
    });
  });

  test("keeps page size and default group updates from the same commit", async () => {
    const onUrlUpdate = vi.fn();
    render(
      <TestUrlState onUrlUpdate={onUrlUpdate}>
        <DataPage
          model="notes.Note"
          columns={[...columns, { field: "updatedAt", header: "Updated At" }]}
          formFields={formFields}
          list={GroupListView}
          pageSize={2}
          defaultGroup={{ field: "updatedAt", granularity: "day" }}
        />
      </TestUrlState>,
    );

    await screen.findByRole("button", { name: "Groups 1-2 / 4 groups" });
    await screen.findByRole("button", { name: "Remove group" });
    await waitFor(() => {
      const latest = onUrlUpdate.mock.calls.at(-1)?.[0];
      expect(latest?.searchParams.get("pageSize")).toBe("2");
      expect(latest?.searchParams.get("group")).toBe("updatedAt:day");
    });
  });

  test("adding a date group level resets a deep page through the data view state", async () => {
    const onUrlUpdate = vi.fn();
    render(
      <TestUrlState
        searchParams="?group=updatedAt:day&page=2&pageSize=2"
        onUrlUpdate={onUrlUpdate}
      >
        <DataPage
          model="notes.Note"
          columns={[...columns, { field: "updatedAt", header: "Updated At" }]}
          formFields={formFields}
          list={GroupListView}
          pageSize={2}
          defaultGroup={{ field: "updatedAt", granularity: "day" }}
        />
      </TestUrlState>,
    );

    await screen.findByRole("button", { name: "Groups 3-4 / 4 groups" });
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Filter, group, favorites",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Month" }));

    await waitFor(() =>
      expect(screen.getByText("Updated · Month")).toBeTruthy(),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Groups 1-2 / 4 groups" }),
      ).toBeTruthy(),
    );
    await waitFor(() => {
      const latest = onUrlUpdate.mock.calls.at(-1)?.[0];
      expect(latest?.searchParams.get("group")).toBe("updatedAt:day");
      expect(latest?.searchParams.get("then")).toBe("updatedAt:month");
      expect(latest?.searchParams.get("page")).not.toBe("2");
    });
  });

  test("expands same-field date group chains with nested filter conjunctions", async () => {
    sdkMocks.groupByCalls.length = 0;
    const dateFilter = encodeURIComponent(
      JSON.stringify({ updatedAt: { gte: "2026-01-01T00:00:00.000Z" } }),
    );
    render(
      <TestUrlState
        searchParams={`?filter=${dateFilter}&group=updatedAt:year&then=updatedAt:day`}
      >
        <DataPage
          model="notes.Note"
          columns={[...columns, { field: "updatedAt", header: "Updated At" }]}
          formFields={formFields}
          list={GroupListView}
        />
      </TestUrlState>,
    );

    const yearGroups = await screen.findAllByRole("button", { name: /2026/ });
    fireEvent.click(yearGroups[0]!);

    await waitFor(() =>
      expect(
        sdkMocks.groupByCalls.some(
          (call) => call.dimensions[0]?.granularity === "DAY",
        ),
      ).toBe(true),
    );
    const branchCall = sdkMocks.groupByCalls.find(
      (call) => call.dimensions[0]?.granularity === "DAY",
    );
    const branchFilter = branchCall?.filter as Record<string, unknown>;
    expect(Array.isArray(branchFilter.AND)).toBe(false);
    expect(branchFilter).toMatchObject({
      updatedAt: { gte: "2026-01-01T00:00:00.000Z" },
      AND: { updatedAt: { exact: expect.any(String) } },
    });
  });

  test("lets the seeded default group granularity be changed", async () => {
    render(
      <TestUrlState>
        <DataPage
          model="notes.Note"
          columns={[...columns, { field: "updatedAt", header: "Updated At" }]}
          formFields={formFields}
          list={GroupListView}
          defaultGroup={{ field: "updatedAt", granularity: "day" }}
        />
      </TestUrlState>,
    );

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Filter, group, favorites",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Month" }));

    await waitFor(() =>
      expect(screen.getByText("Updated · Month")).toBeTruthy(),
    );
  });
});

function nextTask(): Promise<void> {
  return new Promise((resolve) => {
    globalThis.setTimeout(resolve, 0);
  });
}

interface TestUrlStateProps {
  children: ReactNode;
  searchParams?: string;
  onUrlUpdate?: (url: URL) => void;
}

const TestUrlStateContext = createContext<TestUrlStateProps | null>(null);

function TestUrlState({
  children,
  searchParams = "",
  onUrlUpdate,
}: TestUrlStateProps): ReactElement {
  const router = useMemo(() => {
    const rootRoute = createRootRoute({ component: TestUrlStateRoot });
    const indexRoute = createRoute({
      getParentRoute: () => rootRoute,
      path: "/",
      component: TestUrlStateScreen,
    });
    return createRouter({
      routeTree: rootRoute.addChildren([indexRoute]),
      history: createMemoryHistory({
        initialEntries: [initialTestUrl(searchParams)],
      }),
      parseSearch: parseFlatSearch,
      stringifySearch: stringifyFlatSearch,
    });
  }, [searchParams]);

  return (
    <TestUrlStateContext.Provider value={{ children, onUrlUpdate }}>
      <RouterProvider router={router} />
    </TestUrlStateContext.Provider>
  );
}

function TestUrlStateRoot(): ReactElement {
  return (
    <ModalsHost>
      <ToastProvider>
        <Outlet />
      </ToastProvider>
    </ModalsHost>
  );
}

function TestUrlStateScreen(): ReactElement {
  const context = useContext(TestUrlStateContext);
  return (
    <>
      <TestUrlStateObserver onUrlUpdate={context?.onUrlUpdate} />
      {context?.children}
    </>
  );
}

function TestUrlStateObserver({
  onUrlUpdate,
}: {
  onUrlUpdate?: (url: URL) => void;
}): null {
  const href = useRouterState({
    select: (state) => state.location.href,
  });
  useEffect(() => {
    onUrlUpdate?.(new URL(href, "https://angee.test"));
  }, [href, onUrlUpdate]);
  return null;
}

function initialTestUrl(searchParams: string): string {
  if (!searchParams) return "/";
  return searchParams.startsWith("?") ? `/${searchParams}` : `/?${searchParams}`;
}
