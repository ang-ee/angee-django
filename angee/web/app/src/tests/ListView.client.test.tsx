// @vitest-environment happy-dom

// Stage F1: a client row-model resource (rowModel:"client") fetches once and
// filters/sorts/paginates/groups in the browser. This proves ListView renders a
// computed `platform.Addon` resource over `useList`, groups it by namespace via
// the flat-list groupRows() machinery, and never issues a server `_groups`
// query (the computed resource exposes no group aggregate).

import {
  act,
  cleanup,
  fireEvent,
  render as rtlRender,
  screen,
  type RenderResult,
} from "@testing-library/react";
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { useMemo, useRef, type ReactElement, type ReactNode } from "react";
import { afterEach, beforeAll, describe, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "@angee/ui/feedback/index";
import { parseFlatSearch, stringifyFlatSearch } from "../create-app";
import { ListView, type ListColumn } from "@angee/ui/views/ListView";
import {
  ModelMetadataProvider,
  type Row,
  type SchemaFieldMetadata,
} from "@angee/resources";

const ADDON_ROWS: readonly Row[] = [
  { id: "a", label: "Notes", namespace: "example", kind: "consumer" },
  { id: "b", label: "Tasks", namespace: "example", kind: "consumer" },
  { id: "c", label: "IAM", namespace: "angee", kind: "base" },
  { id: "d", label: "Storage", namespace: "angee", kind: "base" },
];

const listCalls = vi.hoisted(
  () => ({ value: [] as Array<{ resource?: string; pageSize?: number }> }),
);
const groupByCalls = vi.hoisted(() => ({ value: [] as unknown[] }));

vi.mock("@refinedev/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@refinedev/core")>();
  return {
    ...actual,
    useList: (options?: {
      resource?: string;
      pagination?: { pageSize?: number };
      queryOptions?: { enabled?: boolean };
    }) => {
      const active = options?.queryOptions?.enabled !== false;
      listCalls.value.push({
        resource: options?.resource,
        pageSize: options?.pagination?.pageSize,
      });
      return {
        result: { data: active ? ADDON_ROWS : [], total: active ? ADDON_ROWS.length : undefined },
        query: { isFetching: false, error: null, refetch: vi.fn() },
      };
    },
    useCan: () => ({ data: { can: false }, isLoading: false, error: null }),
    useInvalidate: () => vi.fn(async () => undefined),
    useCustomMutation: () => ({
      mutateAsync: vi.fn(async () => ({ data: null })),
      mutation: { isPending: false, error: null },
    }),
  };
});

// The computed resource has no group aggregate; record any server group query so
// the test can assert none is issued.
vi.mock("@angee/ui/data/hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui/data/hooks")>();
  return {
    ...actual,
    useAngeeFacets: () => ({ facets: [], fetching: false }),
    useAngeeGroupBy: (...args: unknown[]) => {
      groupByCalls.value.push(args);
      return { groups: [], total: undefined, fetching: false, error: null };
    },
  };
});

const columns = [
  { field: "label", header: "Addon" },
  { field: "namespace", header: "Namespace" },
  { field: "kind", header: "Kind" },
] satisfies readonly ListColumn[];

const ADDON_SCHEMA_METADATA: SchemaFieldMetadata = {
  types: {
    AddonType: {
      typeName: "AddonType",
      recordRepresentation: "label",
      rootFields: {
        list: "platform_addons",
        detail: "platform_addons_by_pk",
        aggregate: "platform_addons_aggregate",
      },
      fields: {
        label: { name: "label", kind: "scalar", scalar: "String" },
        namespace: { name: "namespace", kind: "scalar", scalar: "String" },
        kind: { name: "kind", kind: "scalar", scalar: "String" },
      },
      resource: {
        schemaName: "console",
        modelLabel: "platform.Addon",
        appLabel: "platform",
        modelName: "addon",
        publicIdField: "id",
        rowModel: "client",
        roots: {
          list: "platform_addons",
          detail: "platform_addons_by_pk",
          aggregate: "platform_addons_aggregate",
        },
        typeNames: {
          node: "PlatformAddonRow",
          filter: "platform_addons_bool_exp",
          order: "platform_addons_order_by",
        },
        capabilities: ["list", "detail", "aggregate"],
        filterFields: ["label", "namespace", "kind"],
        orderFields: ["label", "namespace", "kind"],
        aggregateFields: [],
        groupByFields: [],
        relationAxes: [],
      },
    },
  },
};

describe("ListView client row model", () => {
  beforeAll(() => {
    Element.prototype.getAnimations ??= () => [];
    Element.prototype.scrollIntoView ??= () => undefined;
  });

  afterEach(async () => {
    await act(async () => {
      cleanup();
      await nextTask();
    });
    listCalls.value.length = 0;
    groupByCalls.value.length = 0;
  });

  test("renders a computed resource over useList and groups by namespace client-side", async () => {
    render(
      <TestUrlState>
        <ListView
          resource="platform.Addon"
          columns={columns}
          defaultGroup={{ field: "namespace" }}
        />
      </TestUrlState>,
    );

    // Grouped client-side by namespace: the fetched set is grouped in the
    // browser into the two namespace buckets, which render as group headers
    // through the flat-list groupRows() machinery (groups start collapsed).
    // Group headers carry the title-cased namespace value (statusLabel).
    const example = (await screen.findByText("Example")).closest("button");
    const angee = screen.getByText("Angee").closest("button");
    expect(example?.getAttribute("aria-expanded")).toBe("false");
    expect(angee?.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("Notes")).toBeNull();

    fireEvent.click(example as HTMLElement);
    expect(example?.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("Notes")).toBeTruthy();
    expect(screen.getByText("Tasks")).toBeTruthy();
    expect(screen.queryByText("IAM")).toBeNull();

    // The client fetch addresses the computed resource once with a high page cap.
    const addonCalls = listCalls.value.filter(
      (call) => call.resource === "platform_addons",
    );
    expect(addonCalls.length).toBeGreaterThan(0);
    expect(addonCalls.every((call) => (call.pageSize ?? 0) >= 1000)).toBe(true);

    // No server _groups query is ever issued for the computed resource.
    expect(groupByCalls.value.length).toBe(0);
  });
});

function render(ui: ReactElement): RenderResult {
  return rtlRender(
    <ModelMetadataProvider metadata={ADDON_SCHEMA_METADATA}>
      {ui}
    </ModelMetadataProvider>,
  );
}

function nextTask(): Promise<void> {
  return new Promise((resolve) => {
    globalThis.setTimeout(resolve, 0);
  });
}

function TestUrlState({ children }: { children: ReactNode }): ReactElement {
  // Keep the router stable (created once) while always rendering current
  // children through a ref, so router URL state survives re-renders.
  const childrenRef = useRef(children);
  childrenRef.current = children;
  const router = useMemo(() => {
    const rootRoute = createRootRoute({ component: TestRoot });
    const indexRoute = createRoute({
      getParentRoute: () => rootRoute,
      path: "/",
      component: () => <>{childrenRef.current}</>,
    });
    return createRouter({
      routeTree: rootRoute.addChildren([indexRoute]),
      history: createMemoryHistory({ initialEntries: ["/"] }),
      parseSearch: parseFlatSearch,
      stringifySearch: stringifyFlatSearch,
    });
  }, []);
  return <RouterProvider router={router} />;
}

function TestRoot(): ReactElement {
  return (
    <ModalsHost>
      <ToastProvider>
        <Outlet />
      </ToastProvider>
    </ModalsHost>
  );
}
