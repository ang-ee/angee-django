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

import { parseFlatSearch, stringifyFlatSearch } from "../createApp";
import { ModalsHost, ToastProvider } from "../feedback";
import { DataPage, NEW_RECORD_ID } from "./DataPage";
import type { ListComponent } from "./List";
import type { FormField } from "./FormView";
import type { ListColumn, ListViewProps } from "./ListView";
import type {
  Row,
  ResourceTypeName,
  UseResourceListOptions,
  UseResourceListResult,
} from "@angee/data";

const sdkMocks = vi.hoisted(() => ({
  rows: [
    { id: "note-1", title: "First" },
    { id: "note-2", title: "Second" },
  ] satisfies Row[],
  recordCalls: [] as Array<{
    model: string;
    id: string | null;
    options: unknown;
  }>,
  mutate: vi.fn(async ({ data }: { data: Row }) => ({
    id: "note-created",
    ...data,
  })),
}));

vi.mock("@angee/sdk", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/sdk")>();
  return {
    ...actual,
    useWidget: () => undefined,
  };
});

vi.mock("@angee/data", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/data")>();
  return {
    ...actual,
    useResourceMutation: () => [
      sdkMocks.mutate,
      { fetching: false, error: null },
    ],
    useResourceRecord: (model: string, id: string | null, options: unknown) => {
      sdkMocks.recordCalls.push({ model, id, options });
      return {
        record: sdkMocks.rows.find((row) => row.id === id) ?? null,
        fetching: false,
        error: null,
        refetch: vi.fn(),
      };
    },
    useResourceList: (
      _model: string,
      options: UseResourceListOptions<ResourceTypeName>,
    ): UseResourceListResult => {
      const active = options.enabled !== false;
      const rows = active ? sdkMocks.rows : [];
      return {
        rows,
        total: rows.length,
        page: 1,
        pageSize: options.pageSize ?? 50,
        pageCount: 1,
        pageInfo: undefined,
        hasNext: false,
        hasPrev: false,
        setPage: vi.fn(),
        firstPage: vi.fn(),
        nextPage: vi.fn(),
        prevPage: vi.fn(),
        lastPage: vi.fn(),
        fetching: false,
        error: null,
        refetch: vi.fn(),
      };
    },
    useAngeeFacets: () => ({
      facets: {},
      fetching: false,
      error: null,
      refetch: vi.fn(),
    }),
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
    sdkMocks.recordCalls.length = 0;
    sdkMocks.mutate.mockClear();
    await act(async () => {
      cleanup();
      await nextTask();
    });
  });

  describe("controlled record mode", () => {
    test("treats NEW_RECORD_ID as create mode without fetching an id named new", async () => {
      render(
        <TestRecordRoutes initialPath="/notes">
          <DataPage
            model="notes.Note"
            columns={columns}
            formFields={formFields}
            recordId={NEW_RECORD_ID}
          />
        </TestRecordRoutes>,
      );

      expect((await screen.findByLabelText("Title") as HTMLInputElement).value)
        .toBe("");
      expect(sdkMocks.recordCalls).not.toEqual(
        expect.arrayContaining([expect.objectContaining({ id: NEW_RECORD_ID })]),
      );
      expect(sdkMocks.recordCalls.at(-1)).toMatchObject({
        model: "notes.Note",
        id: null,
        options: { enabled: false },
      });
    });
  });

  describe("routed record mode", () => {
    test("derives the collection base path by stripping the matched route param segment", async () => {
      const captured: { current: ListViewProps<Row> | null } = { current: null };
      const CapturingList: ListComponent<Row> = (props) => {
        captured.current = props;
        return <div data-testid="captured-list" />;
      };

      render(
        <TestRecordRoutes initialPath="/notes/note-1">
          <DataPage
            model="notes.Note"
            columns={columns}
            formFields={formFields}
            list={CapturingList}
            placement="drawer"
            routed
          />
        </TestRecordRoutes>,
      );

      expect(await screen.findByTestId("captured-list")).toBeTruthy();
      expect(captured.current?.rowHref?.({ id: "note 2", title: "Second" }))
        .toBe("/notes/note%202");
      expect(await screen.findByDisplayValue("First")).toBeTruthy();
    });

    test("preserves collection search when opening routed records", async () => {
      const updates: URL[] = [];
      const captured: { current: ListViewProps<Row> | null } = { current: null };
      const CapturingList: ListComponent<Row> = (props) => {
        captured.current = props;
        return (
          <button
            type="button"
            onClick={() => props.onRowClick?.(sdkMocks.rows[1]!)}
          >
            Open second
          </button>
        );
      };

      render(
        <TestRecordRoutes
          initialPath="/notes?filter=active&page=2"
          onUrlUpdate={(url) => updates.push(url)}
        >
          <DataPage
            model="notes.Note"
            columns={columns}
            formFields={formFields}
            list={CapturingList}
            routed
          />
        </TestRecordRoutes>,
      );

      await screen.findByRole("button", { name: "Open second" });
      const href = captured.current?.rowHref?.({ id: "note 2", title: "Second" });
      expect(href).toBe("/notes/note%202?filter=active&page=2");

      fireEvent.click(screen.getByRole("button", { name: "Open second" }));
      await waitFor(() => {
        const latest = updates.at(-1);
        expect(latest?.pathname).toBe("/notes/note-2");
        expect(latest?.searchParams.get("filter")).toBe("active");
        expect(latest?.searchParams.get("page")).toBe("2");
      });
    });

    test("rejects routed mode mixed with controlled record props", () => {
      expect(() =>
        render(
          <DataPage
            model="notes.Note"
            columns={columns}
            formFields={formFields}
            recordId="note-1"
            routed
          />,
        ),
      ).toThrow(/routed mode cannot mix with controlled record props: recordId/);
    });

    test("throws when the matched route has no trailing record param", async () => {
      render(
        <TestRecordRoutes initialPath="/notes" withRecordRoute={false}>
          <DataPage
            model="notes.Note"
            columns={columns}
            formFields={formFields}
            routed
          />
        </TestRecordRoutes>,
      );

      const alert = await screen.findByRole("alert");
      expect(alert.textContent).toMatch(
        /DataPage routed mode on route ".+" needs a trailing \$param child route/,
      );
    });

    test("keeps routed handlers and rowHref stable across a no-op re-render", async () => {
      const captures: ListViewProps<Row>[] = [];
      const CapturingList: ListComponent<Row> = (props) => {
        captures.push(props);
        return <div data-testid="capturing-list" />;
      };

      function Harness(): ReactElement {
        const [, setTick] = useState(0);
        return (
          <>
            <button type="button" onClick={() => setTick((tick) => tick + 1)}>
              Re-render
            </button>
            <DataPage
              model="notes.Note"
              columns={columns}
              formFields={formFields}
              list={CapturingList}
              routed
            />
          </>
        );
      }

      render(
        <TestRecordRoutes initialPath="/notes">
          <Harness />
        </TestRecordRoutes>,
      );

      await screen.findByTestId("capturing-list");
      const initial = captures.at(-1)!;
      fireEvent.click(screen.getByRole("button", { name: "Re-render" }));

      await waitFor(() => expect(captures.length).toBeGreaterThan(1));
      const next = captures.at(-1)!;
      expect(next.onCreate).toBe(initial.onCreate);
      expect(next.onRowClick).toBe(initial.onRowClick);
      expect(next.rowHref).toBe(initial.rowHref);
    });

    test("navigates select, close, and create through the routed base path", async () => {
      const updates: URL[] = [];
      const CapturingList: ListComponent<Row> = (props) => (
        <div data-testid="capturing-list">
          <button
            type="button"
            onClick={() => props.onRowClick?.(sdkMocks.rows[1]!)}
          >
            Open second
          </button>
          <button type="button" onClick={() => props.onCreate?.()}>
            Create routed record
          </button>
        </div>
      );

      render(
        <TestRecordRoutes
          initialPath="/notes"
          onUrlUpdate={(url) => updates.push(url)}
        >
          <DataPage
            model="notes.Note"
            columns={columns}
            formFields={formFields}
            list={CapturingList}
            routed
          />
        </TestRecordRoutes>,
      );

      fireEvent.click(await screen.findByRole("button", { name: "Open second" }));
      await waitFor(() =>
        expect(updates.at(-1)?.pathname).toBe("/notes/note-2"),
      );
      expect(await screen.findByDisplayValue("Second")).toBeTruthy();

      const switcher = await screen.findByRole("group", {
        name: "Record view switcher",
      });
      fireEvent.click(
        within(switcher).getByRole("button", { name: "Board view" }),
      );
      await waitFor(() => {
        const latest = updates.at(-1);
        expect(latest?.pathname).toBe("/notes");
        expect(latest?.searchParams.get("view")).toBe("board");
      });

      fireEvent.click(
        await screen.findByRole("button", { name: "Create routed record" }),
      );
      await waitFor(() => expect(updates.at(-1)?.pathname).toBe("/notes/new"));
      expect((await screen.findByLabelText("Title") as HTMLInputElement).value)
        .toBe("");
    });
  });
});

function nextTask(): Promise<void> {
  return new Promise((resolve) => {
    globalThis.setTimeout(resolve, 0);
  });
}

interface TestRecordRoutesProps {
  children: ReactNode;
  initialPath: string;
  onUrlUpdate?: (url: URL) => void;
  withRecordRoute?: boolean;
}

const TestRecordRoutesContext =
  createContext<TestRecordRoutesProps | null>(null);

function TestRecordRoutes({
  children,
  initialPath,
  onUrlUpdate,
  withRecordRoute = true,
}: TestRecordRoutesProps): ReactElement {
  const router = useMemo(() => {
    const rootRoute = createRootRoute({
      component: TestRootRoute,
      errorComponent: TestRouteError,
    });
    const notesRoute = createRoute({
      getParentRoute: () => rootRoute,
      path: "/notes",
      component: TestRecordRoutesScreen,
      errorComponent: TestRouteError,
    });
    const recordRoute = createRoute({
      getParentRoute: () => notesRoute,
      path: "$id",
    });
    const routeTree = rootRoute.addChildren([
      withRecordRoute ? notesRoute.addChildren([recordRoute]) : notesRoute,
    ]);

    return createRouter({
      routeTree,
      history: createMemoryHistory({ initialEntries: [initialPath] }),
      parseSearch: parseFlatSearch,
      stringifySearch: stringifyFlatSearch,
    });
  }, [initialPath, withRecordRoute]);

  return (
    <TestRecordRoutesContext.Provider
      value={{ children, initialPath, onUrlUpdate }}
    >
      <RouterProvider router={router} />
    </TestRecordRoutesContext.Provider>
  );
}

function TestRouteError({ error }: { error: unknown }): ReactElement {
  const message = error instanceof Error ? error.message : String(error);
  return <div role="alert">{message}</div>;
}

function TestRootRoute(): ReactElement {
  return (
    <ModalsHost>
      <ToastProvider>
        <Outlet />
      </ToastProvider>
    </ModalsHost>
  );
}

function TestRecordRoutesScreen(): ReactElement {
  const context = useContext(TestRecordRoutesContext);
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
