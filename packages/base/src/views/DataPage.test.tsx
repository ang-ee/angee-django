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
  RouterContextProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import {
  useMemo,
  useState,
  type ComponentProps,
  type ReactElement,
} from "react";
import { NuqsTestingAdapter } from "nuqs/adapters/testing";
import { afterEach, beforeAll, describe, expect, test, vi } from "vitest";

import {
  Breadcrumb,
  BreadcrumbProvider,
} from "../chrome/Breadcrumb";
import { ModalsHost } from "../feedback";
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
    { id: "note-1", title: "First" },
    { id: "note-2", title: "Second" },
    { id: "note-3", title: "Third" },
    { id: "note-4", title: "Fourth" },
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

    const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" });
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

function TestUrlState({
  children,
  hasMemory = true,
  ...props
}: ComponentProps<typeof NuqsTestingAdapter>): ReactElement {
  const router = useMemo(() => {
    const rootRoute = createRootRoute();
    const indexRoute = createRoute({
      getParentRoute: () => rootRoute,
      path: "/",
      component: () => null,
    });
    return createRouter({
      routeTree: rootRoute.addChildren([indexRoute]),
      history: createMemoryHistory({ initialEntries: ["/"] }),
    });
  }, []);

  return (
    <RouterContextProvider router={router}>
      <ModalsHost>
        <NuqsTestingAdapter {...props} hasMemory={hasMemory}>
          {children}
        </NuqsTestingAdapter>
      </ModalsHost>
    </RouterContextProvider>
  );
}
