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
} from "@tanstack/react-router";
import {
  createContext,
  useContext,
  useMemo,
  type ReactElement,
  type ReactNode,
} from "react";
import { afterEach, describe, expect, test, vi } from "vitest";
import {
  AppRuntimeProvider,
  type DeletePreview,
  type Row,
  type UseResourceListResult,
} from "@angee/sdk";

import { baseIcons } from "../chrome/icon-registry";
import { parseFlatSearch, stringifyFlatSearch } from "../createApp";
import { ToastProvider } from "../feedback";
import { DeletePreviewTree } from "./DeletePreviewTree";
import { ListView, type ListColumn } from "./ListView";

const sdkMocks = vi.hoisted(() => ({
  rows: [
    { id: "sale-1", title: "First sale" },
    { id: "sale-2", title: "Second sale" },
  ] satisfies Row[],
  mutate: vi.fn(),
}));

vi.mock("@angee/sdk", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/sdk")>();
  return {
    ...actual,
    useResourceList: (): UseResourceListResult => ({
      rows: sdkMocks.rows,
      total: sdkMocks.rows.length,
      pageCount: 1,
      page: 1,
      pageSize: 50,
      pageInfo: undefined,
      hasNext: false,
      hasPrev: false,
      fetching: false,
      error: null,
      refetch: vi.fn(),
      setPage: vi.fn(),
      firstPage: vi.fn(),
      nextPage: vi.fn(),
      prevPage: vi.fn(),
      lastPage: vi.fn(),
    }),
    useResourceMutation: () => [
      sdkMocks.mutate,
      { fetching: false, error: null },
    ],
  };
});

const columns = [
  { field: "title", header: "Title" },
] satisfies readonly ListColumn[];

describe("bulk delete flow", () => {
  afterEach(async () => {
    await act(async () => {
      cleanup();
      await nextTask();
    });
    sdkMocks.mutate.mockReset();
  });

  test("SelectionBar shows Delete when rows are selected", async () => {
    sdkMocks.mutate.mockResolvedValue(previewFor("sale-1", "First sale"));

    render(
      <TestUrlState>
        <ListView model="sales.Sale" columns={columns} />
      </TestUrlState>,
    );

    fireEvent.click((await screen.findAllByRole("checkbox", { name: "Select row" }))[0]!);

    expect(screen.getByText("1 selected")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Delete" })).toBeTruthy();
  });

  test("clicking Delete runs dry-run preview and opens the tree dialog", async () => {
    sdkMocks.mutate.mockResolvedValue(previewFor("sale-1", "First sale"));

    render(
      <TestUrlState>
        <ListView model="sales.Sale" columns={columns} />
      </TestUrlState>,
    );

    fireEvent.click((await screen.findAllByRole("checkbox", { name: "Select row" }))[0]!);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() =>
      expect(sdkMocks.mutate).toHaveBeenCalledWith({ id: "sale-1", confirm: false }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Delete 1 records?")).toBeTruthy();
    expect(within(dialog).getByText("First sale")).toBeTruthy();
    expect(within(dialog).getByRole("button", { name: "2 line items" })).toBeTruthy();
    expect(within(dialog).getByText("Line 1")).toBeTruthy();
  });

  test("confirming deletes and clears selection", async () => {
    sdkMocks.mutate.mockResolvedValue(previewFor("sale-1", "First sale"));

    render(
      <TestUrlState>
        <ListView model="sales.Sale" columns={columns} />
      </TestUrlState>,
    );

    fireEvent.click((await screen.findAllByRole("checkbox", { name: "Select row" }))[0]!);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() =>
      expect(sdkMocks.mutate).toHaveBeenCalledWith({ id: "sale-1", confirm: true }),
    );
    await waitFor(() => expect(screen.queryByText("1 selected")).toBeNull());
  });

  test("tree renders nested nodes collapsibly", () => {
    render(
      <TestShell>
        <DeletePreviewTree nodes={[previewFor("sale-1", "First sale").root]} />
      </TestShell>,
    );

    expect(screen.getByText("Line 1")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "2 line items" }));

    expect(screen.queryByText("Line 1")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "2 line items" }));

    expect(screen.getByText("Line 1")).toBeTruthy();
  });
});

function previewFor(id: string, objectLabel: string): DeletePreview {
  return {
    totalDeletedCount: 3,
    hasBlockers: false,
    deleted: [
      { label: "sales", count: 1 },
      { label: "line items", count: 2 },
    ],
    updated: [],
    blocked: [],
    root: {
      label: "sale",
      objectLabel,
      objectId: id,
      children: [
        {
          label: "line items",
          objectLabel: "2 line items",
          objectId: null,
          children: [
            {
              label: "line item",
              objectLabel: "Line 1",
              objectId: "line-1",
              children: [],
            },
          ],
        },
      ],
    },
  };
}

function nextTask(): Promise<void> {
  return new Promise((resolve) => {
    globalThis.setTimeout(resolve, 0);
  });
}

const TestUrlStateContext = createContext<{ children: ReactNode } | null>(null);

function TestUrlState({ children }: { children: ReactNode }): ReactElement {
  const router = useMemo(() => {
    const rootRoute = createRootRoute({ component: TestRootRoute });
    const indexRoute = createRoute({
      getParentRoute: () => rootRoute,
      path: "/",
      component: TestScreen,
    });
    return createRouter({
      routeTree: rootRoute.addChildren([indexRoute]),
      history: createMemoryHistory({ initialEntries: ["/"] }),
      parseSearch: parseFlatSearch,
      stringifySearch: stringifyFlatSearch,
    });
  }, []);

  return (
    <TestUrlStateContext.Provider value={{ children }}>
      <RouterProvider router={router} />
    </TestUrlStateContext.Provider>
  );
}

function TestRootRoute(): ReactElement {
  return (
    <TestShell>
      <Outlet />
    </TestShell>
  );
}

function TestScreen(): ReactElement | null {
  const context = useContext(TestUrlStateContext);
  return context ? <>{context.children}</> : null;
}

function TestShell({ children }: { children: ReactNode }): ReactElement {
  return (
    <AppRuntimeProvider runtime={{ icons: baseIcons }}>
      <ToastProvider>{children}</ToastProvider>
    </AppRuntimeProvider>
  );
}
