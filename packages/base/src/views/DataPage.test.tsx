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

import {
  Breadcrumb,
  BreadcrumbProvider,
} from "../chrome/Breadcrumb";
import { ModalsHost } from "../feedback";
import { parseFlatSearch, stringifyFlatSearch } from "../createApp";
import { DataPage } from "./DataPage";
import type { FormField } from "./FormView";
import type { ListColumn } from "./ListView";
import type {
  Row,
  ResourceTypeName,
  UseResourceListOptions,
  UseResourceListResult,
} from "@angee/sdk";

const sdkMocks = vi.hoisted(() => ({
  rows: [
    { id: "note-1", title: "First", status: "ACTIVE", priority: "High" },
    { id: "note-2", title: "Second", status: "ACTIVE", priority: "Low" },
    { id: "note-3", title: "Third", status: "DRAFT", priority: "Medium" },
    { id: "note-4", title: "Fourth", status: "ARCHIVED", priority: "Low" },
  ] satisfies Row[],
  mutate: vi.fn(async ({ data }: { data: Row }) => data),
}));

vi.mock("@angee/sdk", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/sdk")>();
  const ReactRuntime = await import("react");
  return {
    ...actual,
    useResourceList: (
      _model: string,
      options: UseResourceListOptions<ResourceTypeName>,
    ): UseResourceListResult => {
      const pageSize = options.pageSize ?? 50;
      const pageCount = Math.max(
        1,
        Math.ceil(sdkMocks.rows.length / pageSize),
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
      const rows = sdkMocks.rows.slice(
        (visiblePage - 1) * pageSize,
        visiblePage * pageSize,
      );
      const setPage = (next: number) => {
        setPageState(Math.min(pageCount, Math.max(1, Math.floor(next))));
      };
      return {
        rows,
        total: sdkMocks.rows.length,
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
    useResourceGroupBy: () => ({
      count: 0,
      totalCount: 0,
      buckets: [],
      fetching: false,
      error: null,
    }),
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
      "2 of 4",
    );

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
      ).toContain("3 of 4"),
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
          ARCHIVED: "default",
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

  test("publishes persistent breadcrumbs for the selected record", async () => {
    render(
      <TestUrlState>
        <BreadcrumbProvider initialTrail={[{ label: "Notes" }]}>
          <Breadcrumb />
          <DataPage
            model="notes.Note"
            columns={columns}
            formFields={formFields}
            recordId="note-2"
            placement="inline"
            pageSize={2}
          />
        </BreadcrumbProvider>
      </TestUrlState>,
    );

    const breadcrumb = await screen.findByRole("navigation", {
      name: "Breadcrumb",
    });
    await waitFor(() =>
      expect(within(breadcrumb).getByText("Second")).toBeTruthy(),
    );
    expect(within(breadcrumb).getByText("Second").getAttribute("aria-current"))
      .toBe("page");
  });

  test("opens a selected row and keeps the record breadcrumb published", async () => {
    function Harness(): ReactElement {
      const [recordId, setRecordId] = useState<string | null | undefined>(
        undefined,
      );
      return (
        <TestUrlState>
          <BreadcrumbProvider initialTrail={[{ label: "Notes" }]}>
            <Breadcrumb />
            <DataPage
              model="notes.Note"
              columns={columns}
              formFields={formFields}
              recordId={recordId}
              placement="inline"
              pageSize={2}
              onSelect={setRecordId}
            />
          </BreadcrumbProvider>
        </TestUrlState>
      );
    }

    render(<Harness />);

    fireEvent.click(await screen.findByRole("button", { name: "Open First" }));
    const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" });
    await waitFor(() =>
      expect(within(breadcrumb).getByText("First")).toBeTruthy(),
    );
    expect(within(breadcrumb).getByText("First").getAttribute("aria-current"))
      .toBe("page");
  });

  test("lets the seeded default group be cleared", async () => {
    render(
      <TestUrlState>
        <DataPage
          model="notes.Note"
          columns={[...columns, { field: "updatedAt", header: "Updated At" }]}
          formFields={formFields}
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
          pageSize={2}
          defaultGroup={{ field: "updatedAt", granularity: "day" }}
        />
      </TestUrlState>,
    );

    await screen.findByRole("button", { name: "Records 1-2 / 4" });
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Records 3-4 / 4" }),
      ).toBeTruthy(),
    );
    await waitFor(() => {
      const latest = onUrlUpdate.mock.calls.at(-1)?.[0];
      expect(latest?.searchParams.get("page")).toBe("2");
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
          pageSize={2}
          defaultGroup={{ field: "updatedAt", granularity: "day" }}
        />
      </TestUrlState>,
    );

    await screen.findByRole("button", { name: "Records 1-2 / 4" });
    await screen.findByRole("button", { name: "Remove group" });
    await waitFor(() => {
      const latest = onUrlUpdate.mock.calls.at(-1)?.[0];
      expect(latest?.searchParams.get("pageSize")).toBe("2");
      expect(latest?.searchParams.get("group")).toBe("updatedAt:day");
    });
  });

  test("changing the group resets a deep page through the data view state", async () => {
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
          pageSize={2}
          defaultGroup={{ field: "updatedAt", granularity: "day" }}
        />
      </TestUrlState>,
    );

    await screen.findByRole("button", { name: "Records 3-4 / 4" });
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
        screen.getByRole("button", { name: "Records 1-2 / 4" }),
      ).toBeTruthy(),
    );
    await waitFor(() => {
      const latest = onUrlUpdate.mock.calls.at(-1)?.[0];
      expect(latest?.searchParams.get("group")).toBe("updatedAt:month");
      expect(latest?.searchParams.get("page")).not.toBe("2");
    });
  });

  test("lets the seeded default group granularity be changed", async () => {
    render(
      <TestUrlState>
        <DataPage
          model="notes.Note"
          columns={[...columns, { field: "updatedAt", header: "Updated At" }]}
          formFields={formFields}
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
      <Outlet />
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
