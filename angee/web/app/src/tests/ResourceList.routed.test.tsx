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
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactElement,
  type ReactNode,
  } from "react";
import { afterEach,
  beforeAll,
  describe,
  expect,
  test,
  vi } from "vitest";
import {
  ModelMetadataProvider,
} from "@angee/metadata";
import type {
  SchemaFieldMetadata,
} from "@angee/metadata";

import { parseFlatSearch, stringifyFlatSearch } from "../create-app";
import { ModalsHost, ToastProvider } from "@angee/ui/feedback/index";
import { ResourceList, REFINE_CREATE_ID } from "@angee/ui/views/ResourceList";
import type { ListComponent } from "@angee/ui/views/List";
import type { FormField } from "@angee/ui/views/FormView";
import type { ListColumn, ListViewProps } from "@angee/ui/views/ListView";
import type {
  Row,
} from "@angee/metadata";

const sdkMocks = vi.hoisted(() => ({
  rows: [
    { id: "note-1", title: "First" },
    { id: "note-2", title: "Second" },
  ] satisfies Row[],
  recordCalls: [] as Array<{
    resource: string | undefined;
    id: string | number | undefined;
    options: unknown;
  }>,
  mutate: vi.fn(async ({ data }: { data: Row }) => ({
    id: "note-created",
    ...data,
  })),
}));

vi.mock("@angee/ui/runtime", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui/runtime")>();
  return {
    ...actual,
    useWidget: () => undefined,
  };
});

vi.mock("@refinedev/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@refinedev/core")>();
  const mutationResult = (
    mutateAsync: (input: { id?: string | number; values?: Record<string, unknown> }) => Promise<{ data: Row | null }>,
  ) => () => ({
    mutateAsync,
    mutation: { isPending: false, error: null },
    query: { isFetching: false, error: null },
  });
  return {
    ...actual,
    useForm: (options?: {
      resource?: string;
      action?: "create" | "edit";
      id?: string | number;
      queryOptions?: { enabled?: boolean };
    }) => {
      sdkMocks.recordCalls.push({
        resource: options?.resource,
        id: options?.id,
        options,
      });
      const queryEnabled = options?.queryOptions?.enabled !== false;
      const record = queryEnabled
        ? sdkMocks.rows.find((row) => String(row.id) === String(options?.id))
        : undefined;
      return {
        id: options?.id,
        setId: vi.fn(),
        query: {
          data: { data: record },
          isFetching: false,
          error: null,
          refetch: vi.fn(),
        },
        mutation: { isPending: false, error: null, status: "idle" },
        formLoading: false,
        onFinish: async (values: Record<string, unknown>) => ({
          data: await sdkMocks.mutate({
            data: options?.action === "edit"
              ? ({ ...values, id: options?.id } as Row)
              : (values as Row),
          }),
        }),
        redirect: vi.fn(),
        overtime: {},
        autoSaveProps: { status: "idle", data: undefined, error: null },
        onFinishAutoSave: vi.fn(),
      };
    },
    useOne: (options?: {
      resource?: string;
      id?: string | number;
      queryOptions?: { enabled?: boolean };
    }) => {
      sdkMocks.recordCalls.push({
        resource: options?.resource,
        id: options?.id,
        options,
      });
      return {
        result:
          sdkMocks.rows.find((row) => String(row.id) === String(options?.id))
          ?? null,
        query: {
          isFetching: false,
          error: null,
          refetch: vi.fn(),
        },
      };
    },
    useList: (options?: {
      pagination?: { currentPage?: number; pageSize?: number };
      queryOptions?: { enabled?: boolean };
    }) => {
      const pageSize = options?.pagination?.pageSize ?? 50;
      const requestedPage = options?.pagination?.currentPage ?? 1;
      const active = options?.queryOptions?.enabled !== false;
      const pageCount = Math.max(1, Math.ceil(sdkMocks.rows.length / pageSize));
      const page = Math.min(pageCount, Math.max(1, requestedPage));
      const rows = active
        ? sdkMocks.rows.slice((page - 1) * pageSize, page * pageSize)
        : [];
      return {
        result: { data: rows, total: active ? sdkMocks.rows.length : undefined },
        query: { isFetching: false, error: null, refetch: vi.fn() },
      };
    },
    useCreate: mutationResult(async ({ values = {} }) => ({
      data: await sdkMocks.mutate({ data: values as Row }),
    })),
    useUpdate: mutationResult(async ({ id, values = {} }) => ({
      data: await sdkMocks.mutate({ data: { ...values, id } as Row }),
    })),
    useCustom: () => ({
      result: { data: undefined },
      query: { isFetching: false, error: null, refetch: vi.fn() },
    }),
    useCustomMutation: () => ({
      mutateAsync: async ({ values }: { values?: Record<string, unknown> }) => ({
        data: await sdkMocks.mutate({ data: (values ?? {}) as Row }),
      }),
      mutation: { isPending: false, error: null },
    }),
    useCan: () => ({
      data: { can: true },
      isLoading: false,
      error: null,
    }),
    useInvalidate: () => vi.fn(async () => undefined),
  };
});

vi.mock("@refinedev/react-hook-form", async () => {
  const hookForm = await import("react-hook-form");
  return {
    useForm: (options: {
      defaultValues?: Record<string, unknown>;
      refineCoreProps?: {
        resource?: string;
        action?: "create" | "edit";
        id?: string | number;
        queryOptions?: { enabled?: boolean };
      };
    } = {}) => {
      const form = hookForm.useForm({ defaultValues: options.defaultValues });
      sdkMocks.recordCalls.push({
        resource: options.refineCoreProps?.resource,
        id: options.refineCoreProps?.id,
        options: options.refineCoreProps,
      });
      const queryEnabled =
        options.refineCoreProps?.queryOptions?.enabled !== false;
      const record = queryEnabled
        ? sdkMocks.rows.find(
            (row) => String(row.id) === String(options.refineCoreProps?.id),
          )
        : undefined;
      const refineCore = {
        id: options.refineCoreProps?.id,
        setId: vi.fn(),
        query: {
          data: { data: record },
          isFetching: false,
          error: null,
          refetch: vi.fn(),
        },
        mutation: { isPending: false, error: null, status: "idle" },
        formLoading: false,
        onFinish: async (values: Record<string, unknown>) => ({
          data: await sdkMocks.mutate({
            data: options.refineCoreProps?.action === "edit"
              ? ({ ...values, id: options.refineCoreProps?.id } as Row)
              : (values as Row),
          }),
        }),
        redirect: vi.fn(),
        overtime: {},
        autoSaveProps: { status: "idle", data: undefined, error: null },
        onFinishAutoSave: vi.fn(),
      };
      return {
        ...form,
        refineCore,
        saveButtonProps: {
          disabled: false,
          onClick: (event: unknown) => {
            void form.handleSubmit((values) => refineCore.onFinish(values))(
              event as never,
            );
          },
        },
      };
    },
  };
});

vi.mock("@refinedev/react-table", async () => {
  const TanStackTable = await import("@tanstack/react-table");
  return {
    useTable: (options: {
      columns?: unknown[];
      state?: { pagination?: { pageIndex?: number; pageSize?: number } };
      getRowId?: (row: Row, index: number) => string;
      refineCoreProps?: {
        pagination?: { currentPage?: number; pageSize?: number };
        queryOptions?: { enabled?: boolean };
      };
    }) => {
      const props = options.refineCoreProps ?? {};
      const pageSize =
        props.pagination?.pageSize ?? options.state?.pagination?.pageSize ?? 50;
      const requestedPage =
        props.pagination?.currentPage
        ?? ((options.state?.pagination?.pageIndex ?? 0) + 1);
      const active = props.queryOptions?.enabled !== false;
      const pageCount = Math.max(1, Math.ceil(sdkMocks.rows.length / pageSize));
      const page = Math.min(pageCount, Math.max(1, requestedPage));
      const rows = active
        ? sdkMocks.rows.slice((page - 1) * pageSize, page * pageSize)
        : [];
      const pagination = {
        pageIndex: options.state?.pagination?.pageIndex ?? page - 1,
        pageSize,
      };
      const reactTable = TanStackTable.createTable({
        data: rows,
        columns: (options.columns ?? []) as never[],
        getCoreRowModel: TanStackTable.getCoreRowModel(),
        getRowId: options.getRowId as never,
        state: {
          ...(options.state ?? {}),
          columnPinning: { left: [], right: [] },
          pagination,
          rowSelection: {},
        },
        onStateChange: () => undefined,
        renderFallbackValue: null,
      });
      return {
        reactTable,
        refineCore: {
          result: { data: rows, total: active ? sdkMocks.rows.length : undefined },
          tableQuery: {
            isFetching: false,
            error: null,
            refetch: vi.fn(),
          },
        },
      };
    },
  };
});

const columns = [
  { field: "title", header: "Title" },
] satisfies readonly ListColumn[];

const formFields = [
  { name: "title", label: "Title", title: true },
] satisfies readonly FormField[];

describe("ResourceList", () => {
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
    test("treats REFINE_CREATE_ID as create mode without fetching an id named new", async () => {
      render(
        <TestRecordRoutes initialPath="/notes">
          <ResourceList
            resource="notes.Note"
            columns={columns}
            formFields={formFields}
            recordId={REFINE_CREATE_ID}
          />
        </TestRecordRoutes>,
      );

      expect((await screen.findByLabelText("Title") as HTMLInputElement).value)
        .toBe("");
      expect(sdkMocks.recordCalls).not.toEqual(
        expect.arrayContaining([expect.objectContaining({ id: REFINE_CREATE_ID })]),
      );
      expect(sdkMocks.recordCalls.at(-1)).toMatchObject({
        resource: "notes",
        id: undefined,
        options: { queryOptions: { enabled: false } },
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
          <ResourceList
            resource="notes.Note"
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
          <ResourceList
            resource="notes.Note"
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
          <ResourceList
            resource="notes.Note"
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
          <ResourceList
            resource="notes.Note"
            columns={columns}
            formFields={formFields}
            routed
          />
        </TestRecordRoutes>,
      );

      const alert = await screen.findByRole("alert");
      expect(alert.textContent).toMatch(
        /ResourceList routed mode on route ".+" needs a trailing \$param child route/,
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
            <ResourceList
              resource="notes.Note"
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
          <ResourceList
            resource="notes.Note"
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
  const queryClient = useMemo(() => createTestQueryClient(), []);
  return (
    <QueryClientProvider client={queryClient}>
      <ModalsHost>
        <ModelMetadataProvider metadata={TEST_SCHEMA_METADATA}>
          <ToastProvider>
            <Outlet />
          </ToastProvider>
        </ModelMetadataProvider>
      </ModalsHost>
    </QueryClientProvider>
  );
}

function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      mutations: { retry: false },
      queries: { retry: false },
    },
  });
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

const TEST_SCHEMA_METADATA: SchemaFieldMetadata = {
  types: {
    NoteType: {
      typeName: "NoteType",
      recordRepresentation: "title",
      fields: {
        title: { name: "title", kind: "scalar", scalar: "String" },
      },
      rootFields: {
        detail: "note",
        list: "notes",
        aggregate: "noteAggregate",
        create: "createNote",
        update: "updateNote",
        delete: "deleteNote",
      },
      resource: {
        schemaName: "console",
        modelLabel: "notes.Note",
        appLabel: "notes",
        modelName: "Note",
        publicIdField: "id",
        roots: {
          list: "notes",
          detail: "note",
          aggregate: "noteAggregate",
          create: "createNote",
          update: "updateNote",
          delete: "deleteNote",
        },
        typeNames: {
          node: "NoteType",
          filter: "NoteFilter",
          order: "NoteOrder",
          aggregate: "NoteAggregate",
        },
        capabilities: ["list", "aggregate", "detail", "create", "update", "delete"],
        filterFields: [],
        orderFields: ["title"],
        aggregateFields: ["id"],
        groupByFields: [],
        relationAxes: [],
      },
    },
  },
};
