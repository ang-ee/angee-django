// @vitest-environment happy-dom

import {
  act,
  cleanup,
  fireEvent,
  render as rtlRender,
  screen,
  waitFor,
  within,
  type RenderOptions,
  type RenderResult,
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

import { ModalsHost,
  ToastProvider } from "@angee/ui/feedback/index";
import { parseFlatSearch,
  stringifyFlatSearch } from "../create-app";
import { ResourceList,
  DrawerResourceList } from "@angee/ui/views/ResourceList";
import { Form } from "@angee/ui/views/Form";
import type { FormField } from "@angee/ui/views/FormView";
import {
  List,
  type ListComponent,
  } from "@angee/ui/views/List";
import {
  ListView,
  type ListColumn,
  type ListViewProps,
  } from "@angee/ui/views/ListView";
import type {
  ResourceListSnapshot,
} from "@angee/ui/views/resource-view-surface";
import type {
  AngeeListBatchScope,
  GroupByBatchScope,
} from "@angee/refine";
import {
  Action,
  Column,
  Facet,
  Field,
  Group,
  } from "@angee/ui/views/page/index";
import {
  type AggregateBucket,
  type AggregateRequestOptions,
  type GroupByRequestOptions,
  type GroupDimension as HasuraGroupDimension,
} from "@angee/refine";
import {
  type Row,
  } from "@angee/metadata";
import {
  ModelMetadataProvider,
} from "@angee/metadata";
import type {
  SchemaFieldMetadata,
} from "@angee/metadata";
import { installTestLocalStorage } from "../testing";

interface ResourceListOptions {
  fields?: readonly string[];
  pageSize?: number;
  page?: number;
  initialPage?: number;
  filter?: unknown;
  order?: unknown;
  enabled?: boolean;
}

type RefineFilter =
  | {
      field: string;
      operator: string;
      value: unknown;
    }
  | {
      field?: never;
      operator: "and" | "or";
      value: RefineFilter[];
    };

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
  aggregateCalls: [] as Array<AggregateRequestOptions & { enabled?: boolean }>,
  groupByCalls: [] as Array<GroupByRequestOptions & { enabled?: boolean }>,
  listCalls: [] as ResourceListOptions[],
  mutate: vi.fn(async ({ data }: { data: Row }) => data),
  fetching: false,
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
    action: "create" | "update",
    mutateAsync: (input: { id?: string | number; values?: Record<string, unknown> }) => Promise<{ data: Row | null }>,
  ) => () => ({
    mutateAsync,
    mutation: { isPending: false, error: null },
    query: { isFetching: false, error: null },
  });
  return {
    ...actual,
    useForm: (options?: {
      action?: "create" | "edit";
      id?: string | number;
      queryOptions?: { enabled?: boolean };
    }) => {
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
    useOne: (options?: { id?: string | number }) => ({
      result:
        sdkMocks.rows.find((row) => String(row.id) === String(options?.id))
        ?? null,
      query: {
        isFetching: false,
        error: null,
        refetch: vi.fn(),
      },
    }),
    useCreate: mutationResult("create", async ({ values = {} }) => ({
      data: await sdkMocks.mutate({ data: values as Row }),
    })),
    useUpdate: mutationResult("update", async ({ id, values = {} }) => ({
      data: await sdkMocks.mutate({ data: { ...values, id } as Row }),
    })),
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
    useList: (options?: {
      pagination?: { currentPage?: number; pageSize?: number };
      filters?: RefineFilter[];
      sorters?:
        | Array<{ field: string; order: "asc" | "desc" }>
        | { initial?: Array<{ field: string; order: "asc" | "desc" }> };
      queryOptions?: { enabled?: boolean };
    }) => {
      const pageSize = options?.pagination?.pageSize ?? 50;
      const requestedPage = options?.pagination?.currentPage ?? 1;
      const active = options?.queryOptions?.enabled !== false;
      const filters = whereFromRefineFilters(options?.filters);
      const order = angeeOrderFromSorters(
        Array.isArray(options?.sorters) ? options.sorters : options?.sorters?.initial,
      );
      sdkMocks.listCalls.push({
        page: requestedPage,
        pageSize,
        filter: filters,
        order,
        enabled: active,
      });
      const matchingRows = active
        ? refineRowsForWhere(sdkMocks.rows, filters)
        : [];
      const pageCount = Math.max(1, Math.ceil(matchingRows.length / pageSize));
      const page = Math.min(pageCount, Math.max(1, requestedPage));
      const rows = matchingRows.slice((page - 1) * pageSize, page * pageSize);
      return {
        result: { data: rows, total: active ? matchingRows.length : undefined },
        query: {
          isFetching: sdkMocks.fetching,
          error: null,
          refetch: vi.fn(),
        },
      };
    },
    useInvalidate: () => vi.fn(async () => undefined),
  };
});

vi.mock("@refinedev/react-hook-form", async () => {
  const hookForm = await import("react-hook-form");
  return {
    useForm: (options: {
      defaultValues?: Record<string, unknown>;
      refineCoreProps?: {
        action?: "create" | "edit";
        id?: string | number;
        queryOptions?: { enabled?: boolean };
      };
    } = {}) => {
      const form = hookForm.useForm({ defaultValues: options.defaultValues });
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

function angeeOrderFromSorters(
  sorters: Array<{ field: string; order: "asc" | "desc" }> | undefined,
): Record<string, "ASC" | "DESC"> | undefined {
  if (!sorters || sorters.length === 0) return undefined;
  return Object.fromEntries(
    sorters.map((sorter) => [
      sorter.field,
      sorter.order === "desc" ? "DESC" : "ASC",
    ]),
  );
}

function whereFromRefineFilters(
  filters: RefineFilter[] | undefined,
): Record<string, unknown> | undefined {
  if (!filters || filters.length === 0) return undefined;
  if (filters.length === 1) return whereFromRefineFilter(filters[0]!);
  return { _and: filters.map(whereFromRefineFilter) };
}

function whereFromRefineFilter(filter: RefineFilter): Record<string, unknown> {
  if (typeof filter.field !== "string") {
    return {
      [filter.operator === "or" ? "_or" : "_and"]:
        filter.value.map(whereFromRefineFilter),
    };
  }
  return {
    [filter.field]: {
      [hasuraOperatorForRefineOperator(filter.operator)]: filter.value,
    },
  };
}

function hasuraOperatorForRefineOperator(operator: string): string {
  switch (operator) {
    case "ne":
      return "_neq";
    case "in":
      return "_in";
    case "nin":
      return "_nin";
    case "contains":
      return "_ilike";
    case "gte":
      return "_gte";
    case "gt":
      return "_gt";
    case "lte":
      return "_lte";
    case "lt":
      return "_lt";
    case "eq":
    default:
      return "_eq";
  }
}

function refineRowsForWhere(
  rows: readonly Row[],
  where: unknown,
): readonly Row[] {
  if (!where) return rows;
  return rows.filter((row) => refineMatchesWhere(row, where));
}

function refineMatchesWhere(row: Row, where: unknown): boolean {
  if (!where || typeof where !== "object" || Array.isArray(where)) return true;
  return Object.entries(where as Record<string, unknown>).every(
    ([field, lookup]) => {
      if (field === "AND" || field === "and" || field === "_and") {
        const items = Array.isArray(lookup) ? lookup : [lookup];
        return items.every((item) => refineMatchesWhere(row, item));
      }
      if (field === "OR" || field === "or" || field === "_or") {
        const items = Array.isArray(lookup) ? lookup : [lookup];
        return items.some((item) => refineMatchesWhere(row, item));
      }
      return refineMatchesLookup(refineReadPath(row, field), lookup);
    },
  );
}

function refineMatchesLookup(value: unknown, lookup: unknown): boolean {
  if (!lookup || typeof lookup !== "object" || Array.isArray(lookup)) {
    return value === lookup;
  }
  const record = lookup as Record<string, unknown>;
  if ("exact" in record) return refineEqualValue(value, record.exact);
  if ("_eq" in record) return refineEqualValue(value, record._eq);
  if (Array.isArray(record.inList)) return record.inList.includes(value);
  if (Array.isArray(record._in)) return record._in.includes(value);
  if ("isNull" in record) return (value == null) === Boolean(record.isNull);
  if ("_is_null" in record) return (value == null) === Boolean(record._is_null);
  if ("gte" in record && refineCompareValue(value, record.gte) < 0) return false;
  if ("_gte" in record && refineCompareValue(value, record._gte) < 0) return false;
  if ("gt" in record && refineCompareValue(value, record.gt) <= 0) return false;
  if ("_gt" in record && refineCompareValue(value, record._gt) <= 0) return false;
  if ("lte" in record && refineCompareValue(value, record.lte) > 0) return false;
  if ("_lte" in record && refineCompareValue(value, record._lte) > 0) return false;
  if ("lt" in record && refineCompareValue(value, record.lt) >= 0) return false;
  if ("_lt" in record && refineCompareValue(value, record._lt) >= 0) return false;
  if (typeof record.iContains === "string") {
    return String(value ?? "")
      .toLowerCase()
      .includes(record.iContains.toLowerCase());
  }
  if (typeof record._ilike === "string") {
    return String(value ?? "")
      .toLowerCase()
      .includes(record._ilike.toLowerCase());
  }
  return true;
}

function refineEqualValue(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function refineCompareValue(left: unknown, right: unknown): number {
  const leftTime = refineDateTime(left);
  const rightTime = refineDateTime(right);
  if (leftTime !== null && rightTime !== null) return leftTime - rightTime;
  if (typeof left === "number" && typeof right === "number") return left - right;
  return String(left ?? "").localeCompare(String(right ?? ""));
}

function refineDateTime(value: unknown): number | null {
  if (typeof value !== "string") return null;
  const time = Date.parse(value);
  return Number.isNaN(time) ? null : time;
}

function refineReadPath(row: Row, path: string): unknown {
  let current: unknown = row;
  for (const key of path.split(".")) {
    if (current == null || typeof current !== "object") return undefined;
    const record = current as Record<string, unknown>;
    current = record[key] ?? record[refineSnakeToCamel(key)];
  }
  return current;
}

function refineSnakeToCamel(field: string): string {
  return field
    .toLowerCase()
    .replace(/_([a-z])/g, (_, letter: string) => letter.toUpperCase());
}

vi.mock("@refinedev/react-table", async () => {
  const TanStackTable = await import("@tanstack/react-table");
  return {
    useTable: (options: {
      columns?: unknown[];
      state?: { pagination?: { pageIndex?: number; pageSize?: number } };
      getRowId?: (row: Row, index: number) => string;
      refineCoreProps?: {
        pagination?: { currentPage?: number; pageSize?: number };
        sorters?: { initial?: Array<{ field: string; order: "asc" | "desc" }> };
        filters?: { initial?: RefineFilter[] };
        queryOptions?: { enabled?: boolean };
      };
      onExpandedChange?: (updater: unknown) => void;
      onRowSelectionChange?: (updater: unknown) => void;
    }) => {
      const props = options.refineCoreProps ?? {};
      const pageSize =
        props.pagination?.pageSize ?? options.state?.pagination?.pageSize ?? 50;
      const requestedPage =
        props.pagination?.currentPage
        ?? ((options.state?.pagination?.pageIndex ?? 0) + 1);
      const active = props.queryOptions?.enabled !== false;
      const filters = whereFromRefineFilters(props.filters?.initial);
      const order = angeeOrderFromSorters(props.sorters?.initial);
      sdkMocks.listCalls.push({
        page: requestedPage,
        pageSize,
        filter: filters,
        order,
        enabled: active,
      });
      const matchingRows = active
        ? refineRowsForWhere(sdkMocks.rows, filters)
        : [];
      const pageCount = Math.max(1, Math.ceil(matchingRows.length / pageSize));
      const page = Math.min(pageCount, Math.max(1, requestedPage));
      const rows = matchingRows.slice((page - 1) * pageSize, page * pageSize);
      const reactTable = TanStackTable.useReactTable<Row>({
        data: rows,
        columns: options.columns as never[],
        state: options.state as never,
        getCoreRowModel: TanStackTable.getCoreRowModel(),
        // Mirror the surface's row models: grouping/expansion resolve through
        // TanStack, so the mock must supply the same factories.
        getGroupedRowModel: TanStackTable.getGroupedRowModel(),
        getExpandedRowModel: TanStackTable.getExpandedRowModel(),
        onExpandedChange: options.onExpandedChange as never,
        onRowSelectionChange: options.onRowSelectionChange as never,
        getRowId: options.getRowId,
        autoResetPageIndex: false,
        autoResetExpanded: false,
      });
      return {
        reactTable,
        refineCore: {
          result: { data: rows, total: active ? matchingRows.length : undefined },
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

vi.mock("@angee/refine", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/refine")>();
  function filteredRows(
    rows: readonly Row[],
    filter: unknown,
  ): readonly Row[] {
    if (!filter) return rows;
    return rows.filter((row) => matchesFilter(row, filter));
  }
  function matchesFilter(row: Row, filter: unknown): boolean {
    if (!filter || typeof filter !== "object" || Array.isArray(filter)) return true;
    return Object.entries(filter as Record<string, unknown>).every(
      ([field, lookup]) => {
        if (field === "AND" || field === "and" || field === "_and") {
          const items = Array.isArray(lookup) ? lookup : [lookup];
          return items.every((item) => matchesFilter(row, item));
        }
        if (field === "OR" || field === "or" || field === "_or") {
          const items = Array.isArray(lookup) ? lookup : [lookup];
          return items.some((item) => matchesFilter(row, item));
        }
        return matchesLookup(readPath(row, field), lookup);
      },
    );
  }
  function matchesLookup(value: unknown, lookup: unknown): boolean {
    if (!lookup || typeof lookup !== "object" || Array.isArray(lookup)) {
      return value === lookup;
    }
    const record = lookup as Record<string, unknown>;
    if ("exact" in record) return isEqualValue(value, record.exact);
    if ("_eq" in record) return isEqualValue(value, record._eq);
    if (Array.isArray(record.inList)) return record.inList.includes(value);
    if (Array.isArray(record._in)) return record._in.includes(value);
    if ("isNull" in record) return (value == null) === Boolean(record.isNull);
    if ("_is_null" in record) return (value == null) === Boolean(record._is_null);
    if ("gte" in record && compareValue(value, record.gte) < 0) return false;
    if ("_gte" in record && compareValue(value, record._gte) < 0) return false;
    if ("gt" in record && compareValue(value, record.gt) <= 0) return false;
    if ("_gt" in record && compareValue(value, record._gt) <= 0) return false;
    if ("lte" in record && compareValue(value, record.lte) > 0) return false;
    if ("_lte" in record && compareValue(value, record._lte) > 0) return false;
    if ("lt" in record && compareValue(value, record.lt) >= 0) return false;
    if ("_lt" in record && compareValue(value, record._lt) >= 0) return false;
    if (typeof record.iContains === "string") {
      return String(value ?? "")
        .toLowerCase()
        .includes(record.iContains.toLowerCase());
    }
    if (typeof record._ilike === "string") {
      return String(value ?? "")
        .toLowerCase()
        .includes(record._ilike.toLowerCase());
    }
    return true;
  }
  function isEqualValue(left: unknown, right: unknown): boolean {
    return JSON.stringify(left) === JSON.stringify(right);
  }
  function compareValue(left: unknown, right: unknown): number {
    const leftTime = dateTime(left);
    const rightTime = dateTime(right);
    if (leftTime !== null && rightTime !== null) return leftTime - rightTime;
    if (typeof left === "number" && typeof right === "number") return left - right;
    return String(left ?? "").localeCompare(String(right ?? ""));
  }
  function dateTime(value: unknown): number | null {
    if (typeof value !== "string") return null;
    const time = Date.parse(value);
    return Number.isNaN(time) ? null : time;
  }
  function readPath(row: Row, path: string): unknown {
    let current: unknown = row;
    for (const key of path.split(".")) {
      if (current == null || typeof current !== "object") return undefined;
      const record = current as Record<string, unknown>;
      current = record[key] ?? record[snakeToCamel(key)];
    }
    return current;
  }
  function groupBuckets(
    rows: readonly Row[],
    dimensions: readonly HasuraGroupDimension[],
    measures: GroupByRequestOptions["measures"],
  ): readonly AggregateBucket[] {
    const buckets = new Map<string, AggregateBucket>();
    for (const row of rows) {
      const key: Record<string, unknown> = {};
      for (const dimension of dimensions) {
        const keyField = dimension.key ?? dimension.input;
        const value = groupValue(row, dimension, keyField);
        key[keyField] = value;
        if (dimension.rangeKey) {
          key[dimension.rangeKey] = dateGroupRangeValue(
            value,
            dimension.granularity ?? dimension.input,
          );
        }
      }
      const bucketKey = JSON.stringify(key);
      const current = buckets.get(bucketKey);
      buckets.set(
        bucketKey,
        applyMeasures(
          current ? { ...current, count: current.count + 1 } : { key, count: 1 },
          row,
          measures,
        ),
      );
    }
    return [...buckets.values()];
  }
  function aggregateBucket(
    rows: readonly Row[],
    measures: AggregateRequestOptions["measures"],
  ): AggregateBucket {
    return rows.reduce<AggregateBucket>(
      (bucket, row) => applyMeasures(bucket, row, measures),
      { key: null, count: rows.length },
    );
  }
  function applyMeasures(
    bucket: AggregateBucket,
    row: Row,
    measures: AggregateRequestOptions["measures"],
  ): AggregateBucket {
    let next = bucket;
    for (const measure of measures ?? []) {
      if (measure.op !== "sum" || !measure.field) continue;
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
  function groupValue(
    row: Row,
    dimension: HasuraGroupDimension,
    keyField: string,
  ): unknown {
    const sourceField = sourceFieldForAggregateKey(keyField);
    const value = readPath(row, sourceField);
    return truncatedDateGroupValue(value, dimension.granularity ?? dimension.input) ?? value;
  }
  function truncatedDateGroupValue(value: unknown, input: string): string | null {
    if (typeof value !== "string") return null;
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return null;
    const granularity = input.toLowerCase();
    if (granularity.endsWith("year") || granularity === "year") {
      return new Date(Date.UTC(date.getUTCFullYear(), 0, 1)).toISOString();
    }
    if (granularity.endsWith("month") || granularity === "month") {
      return new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), 1)).toISOString();
    }
    if (granularity.endsWith("day") || granularity === "day") {
      return new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate())).toISOString();
    }
    return null;
  }
  function dateGroupRangeValue(
    value: unknown,
    input: string,
  ): { from: string; to: string } | null {
    if (typeof value !== "string") return null;
    const from = new Date(value);
    if (Number.isNaN(from.getTime())) return null;
    const to = new Date(from.getTime());
    const granularity = input.toLowerCase();
    if (granularity.endsWith("year") || granularity === "year") {
      to.setUTCFullYear(to.getUTCFullYear() + 1);
    } else if (granularity.endsWith("month") || granularity === "month") {
      to.setUTCMonth(to.getUTCMonth() + 1);
    } else if (granularity.endsWith("week") || granularity === "week") {
      to.setUTCDate(to.getUTCDate() + 7);
    } else if (granularity.endsWith("day") || granularity === "day") {
      to.setUTCDate(to.getUTCDate() + 1);
    } else {
      return null;
    }
    return { from: from.toISOString(), to: to.toISOString() };
  }
  function numberValue(value: unknown): number {
    return typeof value === "number" && Number.isFinite(value) ? value : 0;
  }
  function sourceFieldForAggregateKey(key: string): string {
    const field = key.replace(/(?:Day|Week|Month|Quarter|Year)$/, "");
    if (!field.includes("_")) {
      return `${field.charAt(0).toLowerCase()}${field.slice(1)}`;
    }
    return snakeToCamel(field);
  }
  function groupByResult(
    options: GroupByRequestOptions & { enabled?: boolean },
  ) {
    sdkMocks.groupByCalls.push(options);
    if (options.enabled === false || options.dimensions.length === 0) {
      return {
        count: 0,
        totalCount: 0,
        buckets: [],
        fetching: sdkMocks.fetching,
        error: null,
        refetch: vi.fn(),
      };
    }
    const buckets = groupBuckets(
      filteredRows(sdkMocks.rows, options.where),
      options.dimensions,
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
      fetching: sdkMocks.fetching,
      error: null,
      refetch: vi.fn(),
    };
  }
  function listBatchEntry(scope: AngeeListBatchScope) {
    sdkMocks.listCalls.push({
      page: scope.page,
      pageSize: scope.pageSize,
      filter: scope.filter,
      order: scope.order,
      enabled: true,
    });
    const matchingRows = refineRowsForWhere(sdkMocks.rows, scope.filter);
    const pageCount = Math.max(1, Math.ceil(matchingRows.length / scope.pageSize));
    const page = Math.min(pageCount, Math.max(1, scope.page));
    return {
      rows: matchingRows.slice(
        (page - 1) * scope.pageSize,
        page * scope.pageSize,
      ),
      total: matchingRows.length,
      fetching: sdkMocks.fetching,
      error: null,
    };
  }
  function snakeToCamel(field: string): string {
    return field
      .toLowerCase()
      .replace(/_([a-z])/g, (_, letter: string) => letter.toUpperCase());
  }
  return {
    ...actual,
    useAngeeGroupBy: (
      _resource: unknown,
      options: GroupByRequestOptions & { enabled?: boolean },
    ) => groupByResult(options),
    useAngeeGroupByBatch: (
      _resource: unknown,
      scopes: readonly GroupByBatchScope[],
      options: { enabled?: boolean } = {},
    ) =>
      new Map(
        scopes.map((scope) => [
          scope.key,
          groupByResult({
            ...scope.query,
            enabled: options.enabled,
          }),
        ]),
      ),
    useAngeeListBatch: (
      _resource: unknown,
      scopes: readonly AngeeListBatchScope[],
      options: { enabled?: boolean } = {},
    ) =>
      new Map(
        (options.enabled === false ? [] : scopes).map((scope) => [
          scope.key,
          listBatchEntry(scope),
        ]),
      ),
    useAngeeAggregate: (
      _resource: unknown,
      options: AggregateRequestOptions & { enabled?: boolean },
    ) => {
      sdkMocks.aggregateCalls.push(options);
      const active = options.enabled !== false;
      return {
        aggregate: active
          ? aggregateBucket(filteredRows(sdkMocks.rows, options.where), options.measures)
          : null,
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

const TEST_SCHEMA_METADATA: SchemaFieldMetadata = {
  types: {
    NoteType: {
      typeName: "NoteType",
      recordRepresentation: "title",
      rootFields: {
        detail: "note",
        list: "notes",
        aggregate: "noteAggregate",
        groupBy: "noteGroups",
        groupByInput: "NoteGroupBySpec",
        groupOrderInput: "NoteGroupOrder",
        delete: "deleteNote",
      },
      fields: {
        title: { name: "title", kind: "scalar", scalar: "String" },
        status: { name: "status", kind: "scalar", scalar: "String" },
        priority: { name: "priority", kind: "scalar", scalar: "String" },
        wordCount: { name: "wordCount", kind: "scalar", scalar: "Int" },
        updatedAt: { name: "updatedAt", kind: "scalar", scalar: "DateTime" },
      },
      resource: {
        schemaName: "public",
        modelLabel: "notes.Note",
        appLabel: "notes",
        modelName: "note",
        publicIdField: "sqid",
        roots: {
          list: "notes",
          detail: "note",
          aggregate: "noteAggregate",
          groups: "noteGroups",
          deletePreview: "deleteNote",
        },
        typeNames: {
          node: "NoteType",
          filter: "NoteFilter",
          order: "NoteOrder",
          groupBySpec: "NoteGroupBySpec",
          groupKey: "NoteGroupKey",
          groupOrder: "NoteGroupOrder",
        },
        capabilities: ["list", "groups", "aggregate"],
        filterFields: ["title", "status", "priority", "updatedAt"],
        orderFields: ["title", "status", "priority", "updatedAt"],
        defaultSort: [
          { field: "updatedAt", direction: "DESC" },
          { field: "title", direction: "ASC" },
        ],
        aggregateFields: ["id", "wordCount"],
        groupByFields: ["status", "updatedAt"],
        groupDimensions: [
          {
            field: "status",
            input: "STATUS",
            key: "status",
            kind: "column",
            scalar: "String",
            filter: {
              kind: "equality",
              field: "status",
              valueKey: "status",
            },
          },
          {
            field: "updatedAt",
            input: "UPDATED_AT",
            key: "updatedAt",
            kind: "column",
            scalar: "DateTime",
            filter: {
              kind: "equality",
              field: "updatedAt",
              valueKey: "updatedAt",
            },
            extractions: [
              {
                name: "year",
                input: "YEAR",
                key: "updatedAtYear",
                rangeKey: "updatedAtYearRange",
                filter: {
                  kind: "range",
                  field: "updatedAt",
                  valueKey: "updatedAtYear",
                  rangeKey: "updatedAtYearRange",
                },
              },
              {
                name: "month",
                input: "MONTH",
                key: "updatedAtMonth",
                rangeKey: "updatedAtMonthRange",
                filter: {
                  kind: "range",
                  field: "updatedAt",
                  valueKey: "updatedAtMonth",
                  rangeKey: "updatedAtMonthRange",
                },
              },
              {
                name: "day",
                input: "DAY",
                key: "updatedAtDay",
                rangeKey: "updatedAtDayRange",
                filter: {
                  kind: "range",
                  field: "updatedAt",
                  valueKey: "updatedAtDay",
                  rangeKey: "updatedAtDayRange",
                },
              },
            ],
          },
        ],
        relationAxes: [],
      },
    },
    SaleType: {
      typeName: "SaleType",
      fields: {},
      rootFields: {
        detail: "sale",
        list: "sales",
      },
    },
  },
};

const SNAKE_NOTE_SCHEMA_METADATA: SchemaFieldMetadata = {
  types: {
    NoteType: {
      typeName: "NoteType",
      recordRepresentation: "title",
      rootFields: {
        detail: "notes_by_pk",
        list: "notes",
        aggregate: "notes_aggregate",
        groupBy: "notes_groups",
      },
      fields: {
        title: { name: "title", kind: "scalar", scalar: "String" },
        status: { name: "status", kind: "scalar", scalar: "String" },
        updated_at: {
          name: "updated_at",
          kind: "scalar",
          scalar: "DateTime",
        },
      },
      resource: {
        schemaName: "public",
        modelLabel: "notes.Note",
        appLabel: "notes",
        modelName: "note",
        publicIdField: "sqid",
        roots: {
          list: "notes",
          detail: "notes_by_pk",
          aggregate: "notes_aggregate",
          groups: "notes_groups",
        },
        typeNames: {
          node: "NoteType",
          filter: "notes_bool_exp",
          order: "notes_order_by",
          groupBySpec: "NoteTypeGroupBySpec",
          groupKey: "NoteTypeGroupKey",
          groupOrder: "NoteTypeGroupOrder",
        },
        capabilities: ["list", "groups", "aggregate"],
        filterFields: ["title", "status", "updated_at"],
        orderFields: ["title", "status", "updated_at"],
        defaultSort: [
          { field: "updated_at", direction: "DESC" },
          { field: "title", direction: "ASC" },
        ],
        aggregateFields: ["id"],
        groupByFields: ["status", "updated_at"],
        groupDimensions: [
          {
            field: "status",
            input: "STATUS",
            key: "status",
            kind: "column",
            scalar: "String",
            filter: {
              kind: "equality",
              field: "status",
              valueKey: "status",
            },
          },
          {
            field: "updated_at",
            input: "UPDATED_AT",
            key: "updated_at",
            kind: "column",
            scalar: "DateTime",
            filter: {
              kind: "equality",
              field: "updated_at",
              valueKey: "updated_at",
            },
            extractions: [
              {
                name: "day",
                input: "DAY",
                key: "updated_at_day",
                rangeKey: "updated_at_day_range",
                filter: {
                  kind: "range",
                  field: "updated_at",
                  valueKey: "updated_at_day",
                  rangeKey: "updated_at_day_range",
                },
              },
            ],
          },
        ],
        relationAxes: [],
      },
    },
  },
};

function render(
  ui: ReactElement,
  options?: RenderOptions,
): RenderResult {
  return rtlRender(
    <ModelMetadataProvider metadata={TEST_SCHEMA_METADATA}>
      {ui}
    </ModelMetadataProvider>,
    options,
  );
}

function lastListCall(): ResourceListOptions | undefined {
  return sdkMocks.listCalls[sdkMocks.listCalls.length - 1];
}

describe("ResourceList", () => {
  beforeAll(() => {
    Element.prototype.getAnimations ??= () => [];
    installTestLocalStorage();
  });

  afterEach(async () => {
    await act(async () => {
      cleanup();
      await nextTask();
    });
    sdkMocks.listCalls.length = 0;
    sdkMocks.fetching = false;
  });

  test("renders ListView with the resource toolbar and group controls", async () => {
    render(
      <TestUrlState>
        <ListView resource="notes.Note" columns={columns} />
      </TestUrlState>,
    );

    expect(await screen.findByText("First")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Board view" })).toBeTruthy();
    const visibleFieldsButton = screen.getByRole("button", {
      name: "Visible fields",
    });
    expect(visibleFieldsButton.closest("thead")).not.toBeNull();
    expect(
      visibleFieldsButton.closest('section[aria-label="Data controls"]'),
    ).toBeNull();

    fireEvent.click(
      screen.getByRole("button", { name: "Filter, group, favorites" }),
    );

    expect(await screen.findByText("Group by")).toBeTruthy();
  });

  test("scrolls toolbar chips horizontally without moving the picker trigger", async () => {
    const activeFilter = encodeURIComponent(
      JSON.stringify({
        status: { exact: "ACTIVE" },
        priority: { exact: "High" },
      }),
    );

    render(
      <TestUrlState searchParams={`?filter=${activeFilter}&group=updatedAt:day`}>
        <ResourceList
          resource="notes.Note"
          columns={columns}
          formFields={formFields}
        />
      </TestUrlState>,
    );

    await screen.findByText("Updated · Day");
    const trigger = screen.getByRole("button", {
      name: "Filter, group, favorites",
    });
    const input = screen.getByLabelText("Filter records") as HTMLInputElement;
    const scrollableChipLane = input.closest(".scroll-x-contained");

    expect(input.getAttribute("placeholder")).toBeNull();
    expect(input.className).toContain("min-w-0");
    expect(input.className).not.toContain("min-w-[7rem]");
    expect(scrollableChipLane).not.toBeNull();
    expect(scrollableChipLane?.className).toContain("scroll-x-contained");
    expect(scrollableChipLane?.contains(trigger)).toBe(false);

    const lane = scrollableChipLane as HTMLElement;
    Object.defineProperties(lane, {
      clientWidth: { configurable: true, value: 120 },
      scrollWidth: { configurable: true, value: 480 },
    });

    fireEvent.wheel(lane, { deltaY: 160 });

    expect(lane.scrollLeft).toBe(160);

    fireEvent.click(trigger);

    expect(await screen.findByRole("button", { name: "Add custom filter" }))
      .toBeTruthy();
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
        <ResourceList
          resource="notes.Note"
          formFields={formFields}
          list={CapturingList}
          rowHref={rowHref}
        >
          <List
            createLabel="Add note"
            emptyContent="No matching notes."
            filterOptions={[{ id: "active", label: "Active", filter: {} }]}
          >
            <Facet field="author" label="Author" labelField="displayName" />
            <Column field="title" header="Title" />
            <Column
              field="wordCount"
              header="Words"
              align="right"
              aggregate="sum"
            />
          </List>
        </ResourceList>
      </TestUrlState>,
    );

    expect(await screen.findByTestId("captured-list")).toBeTruthy();
    expect(captured.current?.resource).toBe("notes.Note");
    expect(captured.current?.columns).toEqual([
      { field: "title", header: "Title" },
      {
        field: "wordCount",
        header: "Words",
        align: "right",
        aggregate: "sum",
      },
    ]);
    expect(captured.current?.filterOptions).toEqual([
      { id: "active", label: "Active", filter: {} },
    ]);
    expect(captured.current?.facets).toEqual([
      { field: "author", label: "Author", labelField: "displayName" },
    ]);
    expect(captured.current?.createLabel).toBe("Add note");
    expect(captured.current?.emptyContent).toBe("No matching notes.");
    expect(captured.current?.rowHref).toBe(rowHref);
  });

  test("supports a collection-only resource with no form declaration", async () => {
    const captured: { current: ListViewProps<Row> | null } = { current: null };
    const CapturingList: ListComponent<Row> = (props) => {
      captured.current = props;
      return <div data-testid="captured-list" />;
    };

    render(
      <TestUrlState>
        <ResourceList resource="notes.Note" list={CapturingList}>
          <List>
            <Column field="title" header="Title" />
          </List>
        </ResourceList>
      </TestUrlState>,
    );

    expect(await screen.findByTestId("captured-list")).toBeTruthy();
    expect(captured.current?.columns).toEqual([
      { field: "title", header: "Title" },
    ]);
    expect(captured.current?.onCreate).toBeUndefined();
    expect(captured.current?.onRowClick).toBeUndefined();
  });

  test("parses Form child fields and groups into ResourceList form descriptors", async () => {
    render(
      <TestUrlState>
        <ResourceList
          resource="notes.Note"
          columns={columns}
          recordId="note-1"
        >
          <Form>
            <Field name="title" label="Title" title />
            <Group label="Details" columns={2}>
              <Field name="priority" label="Priority" readOnly />
            </Group>
          </Form>
        </ResourceList>
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
        <ResourceList
          resource="notes.Note"
          columns={columns}
          formFields={formFields}
        >
          <Form>
            <Field name="title" />
          </Form>
        </ResourceList>
      ),
      message: /ResourceList and its Form child both declare "formFields"/,
    },
    {
      name: "formGroups plus Form child",
      element: (
        <ResourceList
          resource="notes.Note"
          columns={columns}
          formGroups={[]}
        >
          <Form>
            <Field name="title" />
          </Form>
        </ResourceList>
      ),
      message: /ResourceList and its Form child both declare "formGroups"/,
    },
    {
      name: "columns plus List child",
      element: (
        <ResourceList
          resource="notes.Note"
          columns={columns}
          formFields={formFields}
        >
          <List>
            <Column field="title" />
          </List>
        </ResourceList>
      ),
      message: /ResourceList and its List child both declare "columns"/,
    },
    {
      name: "facets plus List facet child",
      element: (
        <ResourceList
          resource="notes.Note"
          formFields={formFields}
          facets={[{ field: "author" }]}
        >
          <List>
            <Facet field="provider" />
            <Column field="title" />
          </List>
        </ResourceList>
      ),
      message: /ResourceList and its List child both declare "facets"/,
    },
    {
      name: "duplicate List children",
      element: (
        <ResourceList resource="notes.Note" formFields={formFields}>
          <List>
            <Column field="title" />
          </List>
          <List>
            <Column field="status" />
          </List>
        </ResourceList>
      ),
      message: /only one List child/,
    },
    {
      name: "duplicate Form children",
      element: (
        <ResourceList resource="notes.Note" columns={columns}>
          <Form>
            <Field name="title" />
          </Form>
          <Form>
            <Field name="status" />
          </Form>
        </ResourceList>
      ),
      message: /only one Form child/,
    },
    {
      name: "List resource mismatch",
      element: (
        <ResourceList resource="notes.Note" formFields={formFields}>
          <List resource="tasks.Task">
            <Column field="title" />
          </List>
        </ResourceList>
      ),
      message: /does not match ResourceList resource/,
    },
    {
      name: "Form resource mismatch",
      element: (
        <ResourceList resource="notes.Note" columns={columns}>
          <Form resource="tasks.Task">
            <Field name="title" />
          </Form>
        </ResourceList>
      ),
      message: /does not match ResourceList resource/,
    },
    {
      name: "unknown element child",
      element: (
        <ResourceList
          resource="notes.Note"
          columns={columns}
          formFields={formFields}
        >
          <Column field="title" />
        </ResourceList>
      ),
      message: /wrapper components hide the marker/,
    },
    {
      name: "unknown text child",
      element: (
        <ResourceList
          resource="notes.Note"
          columns={columns}
          formFields={formFields}
        >
          text
        </ResourceList>
      ),
      message: /ResourceList child text "text"/,
    },
    {
      name: "empty nested List",
      element: (
        <ResourceList resource="notes.Note" formFields={formFields}>
          <List />
        </ResourceList>
      ),
      message: /requires at least one Column child/,
    },
    {
      name: "forwarded prop overlap",
      element: (
        <ResourceList
          resource="notes.Note"
          formFields={formFields}
          order={{ title: "ASC" }}
        >
          <List order={{ title: "DESC" }}>
            <Column field="title" />
          </List>
        </ResourceList>
      ),
      message: /ResourceList and its List child both declare "order"/,
    },
    {
      name: "ResourceList-owned List wiring",
      element: (
        <ResourceList resource="notes.Note" formFields={formFields}>
          <List onCreate={() => undefined}>
            <Column field="title" />
          </List>
        </ResourceList>
      ),
      message: /ResourceList owns List child "onCreate" wiring/,
    },
    {
      name: "ResourceList-owned Form wiring",
      element: (
        <ResourceList resource="notes.Note" columns={columns}>
          <Form id="note-1">
            <Field name="title" />
          </Form>
        </ResourceList>
      ),
      message: /ResourceList owns Form child "id" wiring/,
    },
  ])("rejects invalid ResourceList declarations: $name", ({ element, message }) => {
    expect(() => render(element)).toThrow(message);
  });

  test.each([
    {
      name: "List without resource",
      element: (
        <List>
          <Column field="title" />
        </List>
      ),
      message: /List requires a resource/,
    },
    {
      name: "Form without resource",
      element: (
        <Form>
          <Field name="title" />
        </Form>
      ),
      message: /Form requires a resource/,
    },
    {
      name: "standalone empty List",
      element: <List resource="notes.Note" />,
      message: /requires at least one Column child/,
    },
  ])("rejects invalid standalone view declarations: $name", ({ element, message }) => {
    expect(() => render(element)).toThrow(message);
  });

  test("renders standalone List from Column children", async () => {
    render(
      <TestUrlState>
        <List resource="notes.Note">
          <Column field="title" header="Title" />
          <Column field="status" header="Status" />
        </List>
      </TestUrlState>,
    );

    expect(await screen.findByText("First")).toBeTruthy();
    expect(screen.getByText("Status")).toBeTruthy();
  });

  test("uses resource metadata default sort as the list order fallback", async () => {
    render(
      <TestUrlState>
        <ResourceList
          resource="notes.Note"
          columns={columns}
          formFields={formFields}
        />
      </TestUrlState>,
    );

    expect(await screen.findByText("First")).toBeTruthy();
    await waitFor(() =>
      expect(lastListCall()?.order).toEqual({
        updatedAt: "DESC",
      }),
    );
  });

  test("keeps explicit order above metadata default sort", async () => {
    render(
      <TestUrlState>
        <ResourceList
          resource="notes.Note"
          columns={columns}
          formFields={formFields}
          order={{ title: "ASC" }}
        />
      </TestUrlState>,
    );

    expect(await screen.findByText("First")).toBeTruthy();
    await waitFor(() =>
      expect(lastListCall()?.order).toEqual({ title: "ASC" }),
    );
  });

  test("keeps URL-owned sort above explicit and metadata default order", async () => {
    render(
      <TestUrlState searchParams="?sort=priority:desc">
        <ResourceList
          resource="notes.Note"
          columns={columns}
          formFields={formFields}
          order={{ title: "ASC" }}
        />
      </TestUrlState>,
    );

    expect(await screen.findByText("First")).toBeTruthy();
    await waitFor(() =>
      expect(lastListCall()?.order).toEqual({ priority: "DESC" }),
    );
  });

  test("DrawerResourceList owns drawer record state and inline controls", async () => {
    render(
      <TestUrlState>
        <DrawerResourceList
          resource="notes.Note"
          columns={columns}
          formFields={formFields}
        />
      </TestUrlState>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "New note" }));

    const dialog = await screen.findByRole("dialog");
    expect((within(dialog).getByLabelText("Title") as HTMLInputElement).value)
      .toBe("");

    fireEvent.click(within(dialog).getByRole("button", { name: "Board view" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  test("renders record navigation and reuses the view switcher in record chrome", async () => {
    const onSelect = vi.fn();
    const onClose = vi.fn();

    function Harness(): ReactElement {
      const [recordId, setRecordId] = useState<string | null>(null);
      return (
        <TestUrlState>
          <ResourceList
            resource="notes.Note"
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

    fireEvent.click(await screen.findByRole("button", { name: "Open Second" }));
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith("note-2"));

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

  test("does not probe list state for a cold direct record", async () => {
    render(
      <TestUrlState>
        <ResourceList
          resource="notes.Note"
          columns={columns}
          formFields={formFields}
          recordId="note-2"
          placement="inline"
          pageSize={2}
        />
      </TestUrlState>,
    );

    expect(await screen.findByDisplayValue("Second")).toBeTruthy();
    expect(
      screen.queryByRole("navigation", { name: "Record navigation" }),
    ).toBeNull();
    expect(sdkMocks.listCalls).toHaveLength(0);
  });

  test("keeps record navigation during a live list refetch", async () => {
    const onSelect = vi.fn();
    const initialSnapshot: ResourceListSnapshot<Row> = {
      rows: [sdkMocks.rows[0]!, sdkMocks.rows[1]!],
      total: 4,
      page: 1,
      pageSize: 2,
      pageCount: 2,
      hasNext: true,
      hasPrev: false,
      fetching: false,
    };
    const fetchingSnapshot: ResourceListSnapshot<Row> = {
      ...initialSnapshot,
      rows: [],
      fetching: true,
    };
    const completedSnapshot: ResourceListSnapshot<Row> = {
      rows: [sdkMocks.rows[0]!, sdkMocks.rows[2]!],
      total: 3,
      page: 1,
      pageSize: 2,
      pageCount: 2,
      hasNext: true,
      hasPrev: false,
      fetching: false,
    };

    function Harness(): ReactElement {
      const [recordId, setRecordId] = useState<string | null>(null);
      const [snapshot, setSnapshot] =
        useState<ResourceListSnapshot<Row>>(initialSnapshot);
      const SnapshotList: ListComponent<Row> = ({
        onListStateChange,
        onRowClick,
      }: ListViewProps<Row>) => {
        useEffect(() => {
          onListStateChange?.(snapshot);
        }, [onListStateChange, snapshot]);
        return (
          <button
            type="button"
            onClick={() => onRowClick?.(sdkMocks.rows[1]!)}
          >
            Open Second
          </button>
        );
      };
      return (
        <TestUrlState>
          <button
            type="button"
            onClick={() => setSnapshot(fetchingSnapshot)}
          >
            Publish fetching snapshot
          </button>
          <button
            type="button"
            onClick={() => setSnapshot(completedSnapshot)}
          >
            Publish completed snapshot
          </button>
          <ResourceList
            resource="notes.Note"
            columns={columns}
            formFields={formFields}
            recordId={recordId}
            list={SnapshotList}
            placement="drawer"
            pageSize={2}
            onSelect={(id) => {
              onSelect(id);
              setRecordId(id);
            }}
          />
        </TestUrlState>
      );
    }

    render(<Harness />);

    fireEvent.click(await screen.findByRole("button", { name: "Open Second" }));
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith("note-2"));

    const pager = await screen.findByRole("navigation", {
      name: "Record navigation",
    });
    expect(pager.textContent?.replace(/\s+/g, " ").trim()).toContain(
      "2 / 4",
    );
    expect(
      within(pager)
        .getByRole("button", { name: "Next record" })
        .hasAttribute("disabled"),
    ).toBe(false);

    fireEvent.click(
      screen.getByRole("button", {
        hidden: true,
        name: "Publish fetching snapshot",
      }),
    );
    await nextTask();
    expect(
      screen
        .getByRole("navigation", { name: "Record navigation" })
        .textContent?.replace(/\s+/g, " ")
        .trim(),
    ).toContain("2 / 4");

    fireEvent.click(
      screen.getByRole("button", {
        hidden: true,
        name: "Publish completed snapshot",
      }),
    );

    await waitFor(() =>
      expect(
        screen
          .getByRole("navigation", { name: "Record navigation" })
          .textContent?.replace(/\s+/g, " ")
          .trim(),
      ).toContain("/ 3"),
    );
  });

  test("folds record actions into the Actions menu", async () => {
    render(
      <TestUrlState>
        <ResourceList
          resource="notes.Note"
          columns={columns}
          recordId="note-2"
          placement="inline"
        >
          <Form>
            <Field name="title" label="Title" title />
            <Action id="archive" label="Archive" set={{ status: "ARCHIVED" }} />
          </Form>
        </ResourceList>
      </TestUrlState>,
    );

    await screen.findByLabelText("Title");
    expect(screen.getAllByRole("button", { name: "Actions" })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Archive" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    expect(await screen.findByRole("menuitem", { name: "Delete" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "Archive" })).toBeTruthy();
  });

  test("omits record Delete when the resource exposes no delete root", async () => {
    render(
      <TestUrlState>
        <NoDeleteMetadata>
          <ResourceList
            resource="sales.Sale"
            columns={columns}
            recordId="note-2"
            placement="inline"
          >
            <Form>
              <Field name="title" label="Title" title />
            </Form>
          </ResourceList>
        </NoDeleteMetadata>
      </TestUrlState>,
    );

    await screen.findByLabelText("Title");
    expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
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
        <ResourceList
          resource="notes.Note"
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

  test("renders board card actions outside the card navigation button", async () => {
    const onSelect = vi.fn();
    const onConnect = vi.fn();
    const boardColumns = [
      { field: "title", header: "Title" },
      { field: "status", header: "Status" },
      { field: "priority", header: "Priority" },
    ] satisfies readonly ListColumn[];

    render(
      <TestUrlState searchParams="?view=board&group=status">
        <ResourceList
          resource="notes.Note"
          columns={boardColumns}
          formFields={formFields}
          onSelect={onSelect}
          cardActions={(row) =>
            row.status === "DRAFT" ? (
              <button type="button" onClick={() => onConnect(row.id)}>
                Connect
              </button>
            ) : null
          }
        />
      </TestUrlState>,
    );

    const draftLane = await screen.findByRole("region", { name: "Draft" });
    const cardButton = within(draftLane).getByRole("button", {
      name: /Third/,
    });
    const connectButton = within(draftLane).getByRole("button", {
      name: "Connect",
    });

    expect(connectButton.closest("button")).toBe(connectButton);
    fireEvent.click(connectButton);
    expect(onConnect).toHaveBeenCalledWith("note-3");
    expect(onSelect).not.toHaveBeenCalled();

    fireEvent.click(cardButton);
    expect(onSelect).toHaveBeenCalledWith("note-3");
  });

  test("keeps board refresh loading out of the list footer", async () => {
    sdkMocks.fetching = true;
    const boardColumns = [
      { field: "title", header: "Title" },
      { field: "status", header: "Status" },
      { field: "priority", header: "Priority" },
    ] satisfies readonly ListColumn[];

    render(
      <TestUrlState searchParams="?view=board">
        <ResourceList
          resource="notes.Note"
          columns={boardColumns}
          formFields={formFields}
        />
      </TestUrlState>,
    );

    expect(await screen.findByRole("region", { name: "All records" }))
      .toBeTruthy();
    expect(screen.queryByText("Loading...")).toBeNull();
    expect(screen.queryByText("list.loading")).toBeNull();
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
        <ResourceList
          resource="notes.Note"
          columns={boardColumns}
          formFields={formFields}
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

  test("seeds the default view without losing explicit list view selection", async () => {
    const onUrlUpdate = vi.fn();
    render(
      <TestUrlState onUrlUpdate={onUrlUpdate}>
        <ResourceList
          resource="notes.Note"
          columns={[
            { field: "title", header: "Title" },
            { field: "status", header: "Status" },
            { field: "updatedAt", header: "Updated At" },
          ]}
          formFields={formFields}
          defaultView="board"
          defaultGroups={{
            list: { field: "updatedAt", granularity: "month" },
            board: { field: "status" },
          }}
        />
      </TestUrlState>,
    );

    await screen.findByRole("region", { name: "Active" });
    await waitFor(() => {
      const latest = onUrlUpdate.mock.calls.at(-1)?.[0];
      expect(latest?.searchParams.get("view")).toBeNull();
      expect(latest?.searchParams.get("group")).toBe("status");
    });

    fireEvent.click(screen.getByRole("button", { name: "List view" }));

    await screen.findByText("Updated · Month");
    await waitFor(() => {
      const latest = onUrlUpdate.mock.calls.at(-1)?.[0];
      expect(latest?.searchParams.get("view")).toBe("list");
      expect(latest?.searchParams.get("group")).toBe("updatedAt:month");
    });
  });

  test("lets the seeded default group be cleared", async () => {
    render(
      <TestUrlState>
        <ResourceList
          resource="notes.Note"
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

  test("renders grouped lists folded and expands group items lazily", async () => {
    const onSelect = vi.fn();

    render(
      <TestUrlState searchParams="?group=status&pageSize=2">
        <ResourceList
          resource="notes.Note"
          columns={columns}
          formFields={formFields}
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

  test("uses the grouped leaf query for record navigation", async () => {
    function Harness(): ReactElement {
      const [recordId, setRecordId] = useState<string | null>(null);
      return (
        <ResourceList
          resource="notes.Note"
          columns={columns}
          formFields={formFields}
          recordId={recordId}
          onSelect={setRecordId}
        />
      );
    }

    render(
      <TestUrlState searchParams="?group=status&pageSize=2">
        <Harness />
      </TestUrlState>,
    );

    const activeGroup = await screen.findByRole("button", { name: /Active/ });
    fireEvent.click(activeGroup);
    fireEvent.click(await screen.findByRole("button", { name: "Open Second" }));

    const pager = await screen.findByRole("navigation", {
      name: "Record navigation",
    });
    expect(pager.textContent?.replace(/\s+/g, " ").trim()).toContain(
      "2 / 2",
    );

    fireEvent.click(
      within(pager).getByRole("button", { name: "Previous record" }),
    );
    await waitFor(() =>
      expect(
        screen
          .getByRole("navigation", { name: "Record navigation" })
          .textContent?.replace(/\s+/g, " ")
          .trim(),
      ).toContain("1 / 2"),
    );
    expect(await screen.findByDisplayValue("First")).toBeTruthy();
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
        <ResourceList
          resource="notes.Note"
          columns={measuredColumns}
          formFields={formFields}
        />
      </TestUrlState>,
    );

    const activeGroup = await screen.findByRole("button", { name: /Active/ });
    expect(within(activeGroup).queryByText(/\bwords\b/i)).toBeNull();
    expect(
      (await screen.findByLabelText("Active Word Count: 30")).textContent,
    ).toBe("30");
    expect(
      (await screen.findByLabelText("Total Word Count: 43")).textContent,
    ).toBe("43");
    expect(
      sdkMocks.groupByCalls.some((call) =>
        call.measures?.some(
          (measure) =>
            measure.op === "sum" && measure.field === "word_count",
        ),
      ),
    ).toBe(true);
    expect(
      sdkMocks.aggregateCalls.some((call) =>
        call.measures?.some(
          (measure) =>
            measure.op === "sum" && measure.field === "word_count",
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
        <ResourceList
          resource="notes.Note"
          columns={[...columns, { field: "updatedAt", header: "Updated At" }]}
          formFields={formFields}
          pageSize={2}
          defaultGroup={{ field: "updatedAt", granularity: "day" }}
        />
      </TestUrlState>,
    );

    await screen.findByRole("button", {
      name: /Groups 1-\d+ \/ \d+ groups/,
    });
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

  test("repairs stale camel-case group search against snake resource metadata", async () => {
    const onUrlUpdate = vi.fn();
    render(
      <TestUrlState
        searchParams="?group=updatedAt:day"
        onUrlUpdate={onUrlUpdate}
      >
        <ModelMetadataProvider metadata={SNAKE_NOTE_SCHEMA_METADATA}>
          <ResourceList
            resource="notes.Note"
            columns={[...columns, { field: "updated_at", header: "Updated At" }]}
            formFields={formFields}
            pageSize={2}
            defaultGroup={{ field: "updated_at", granularity: "day" }}
          />
        </ModelMetadataProvider>
      </TestUrlState>,
    );

    await screen.findByRole("button", {
      name: /Groups 1-\d+ \/ \d+ groups/,
    });
    await waitFor(() => {
      const latest = onUrlUpdate.mock.calls.at(-1)?.[0];
      expect(latest?.searchParams.get("group")).toBe("updated_at:day");
    });
    expect(screen.queryByText("Something went wrong")).toBeNull();
  });

  test("selects page size from the pager range popover", async () => {
    const onUrlUpdate = vi.fn();
    render(
      <TestUrlState onUrlUpdate={onUrlUpdate}>
        <ResourceList
          resource="notes.Note"
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
        <ResourceList
          resource="notes.Note"
          columns={columns}
          formFields={formFields}
        />
      </TestUrlState>,
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "Filter, group, favorites" }),
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
    // A custom text filter defaults to case-sensitive `contains` so it coexists
    // with the free-text search box's `iContains` on the same field; the chip
    // labels that distinction.
    expect(
      await screen.findByText("Title contains (case-sensitive) Fir"),
    ).toBeTruthy();
  });

  test("saves and reapplies the current resource-view search", async () => {
    const onUrlUpdate = vi.fn();
    render(
      <TestUrlState onUrlUpdate={onUrlUpdate}>
        <ResourceList
          resource="notes.Note"
          columns={columns}
          formFields={formFields}
          pageSize={2}
        />
      </TestUrlState>,
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "Filter, group, favorites" }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Save current search" }),
    );
    fireEvent.change(await screen.findByRole("textbox", { name: "Favorite name" }), {
      target: { value: "Two per page" },
    });
    fireEvent.click(await screen.findByRole("button", { name: "Save" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Filter, group, favorites" }),
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
      await screen.findByRole("button", { name: "Filter, group, favorites" }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Two per page" }));
    await waitFor(() => {
      const latest = onUrlUpdate.mock.calls.at(-1)?.[0];
      expect(latest?.searchParams.get("pageSize")).toBeNull();
    });
  });

  test("keeps page defaults out of URL when another default writes search state", async () => {
    const onUrlUpdate = vi.fn();
    render(
      <TestUrlState onUrlUpdate={onUrlUpdate}>
        <ResourceList
          resource="notes.Note"
          columns={[...columns, { field: "updatedAt", header: "Updated At" }]}
          formFields={formFields}
          pageSize={2}
          defaultGroup={{ field: "updatedAt", granularity: "day" }}
        />
      </TestUrlState>,
    );

    await screen.findByRole("button", { name: "Groups 1-2 / 4 groups" });
    await screen.findByRole("button", { name: "Remove group" });
    await waitFor(() => {
      const latest = onUrlUpdate.mock.calls.at(-1)?.[0];
      expect(latest?.searchParams.get("pageSize")).toBeNull();
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
        <ResourceList
          resource="notes.Note"
          columns={[...columns, { field: "updatedAt", header: "Updated At" }]}
          formFields={formFields}
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
        <ResourceList
          resource="notes.Note"
          columns={[...columns, { field: "updatedAt", header: "Updated At" }]}
          formFields={formFields}
        />
      </TestUrlState>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "2026" }));

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
    const branchWhere = branchCall?.where as Record<string, unknown>;
    expect(branchWhere).toMatchObject({
      updatedAt: { _gte: "2026-01-01T00:00:00.000Z" },
      _and: [
        {
          updatedAt: {
            _gte: "2026-01-01T00:00:00.000Z",
            _lt: "2027-01-01T00:00:00.000Z",
          },
        },
      ],
    });
  });

  test("lets the seeded default group granularity be changed", async () => {
    render(
      <TestUrlState>
        <ResourceList
          resource="notes.Note"
          columns={[...columns, { field: "updatedAt", header: "Updated At" }]}
          formFields={formFields}
          defaultGroup={{ field: "updatedAt", granularity: "day" }}
        />
      </TestUrlState>,
    );

    await screen.findByRole("button", { name: "Groups 1-4 / 4 groups" });
    await act(async () => {
      await nextTask();
    });
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
  const queryClient = useMemo(() => createTestQueryClient(), []);
  return (
    <QueryClientProvider client={queryClient}>
      <ModalsHost>
        <ToastProvider>
          <Outlet />
        </ToastProvider>
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

function TestUrlStateScreen(): ReactElement {
  const context = useContext(TestUrlStateContext);
  return (
    <>
      <TestUrlStateObserver onUrlUpdate={context?.onUrlUpdate} />
      {context?.children}
    </>
  );
}

function NoDeleteMetadata({ children }: { children: ReactNode }): ReactElement {
  return (
    <ModelMetadataProvider
      metadata={{
        types: {
          SaleType: {
            typeName: "SaleType",
            fields: {},
            rootFields: {
              detail: "sale",
              list: "sales",
              aggregate: "saleAggregate",
            },
            resource: {
              schemaName: "public",
              modelLabel: "sales.Sale",
              appLabel: "sales",
              modelName: "sale",
              publicIdField: "id",
              roots: {
                list: "sales",
                detail: "sale",
                aggregate: "saleAggregate",
              },
              typeNames: {
                node: "SaleType",
                filter: "SaleFilter",
                order: "SaleOrder",
                aggregate: "SaleAggregate",
              },
              capabilities: ["list", "aggregate", "detail"],
              fields: [],
              filterFields: [],
              orderFields: [],
              aggregateFields: ["id"],
              groupByFields: [],
              relationAxes: [],
            },
          },
        },
      }}
    >
      {children}
    </ModelMetadataProvider>
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
