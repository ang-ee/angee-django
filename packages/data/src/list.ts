import { useCallback, useEffect, useMemo, useState } from "react";
import {
  useList,
  type BaseRecord,
  type CrudFilter,
  type CrudFilters,
  type CrudSorting,
  type Fields,
  type HttpError,
  type LogicalFilter,
} from "@refinedev/core";
import {
  useModelMetadata,
} from "@angee/sdk";

import {
  DEFAULT_PAGE_SIZE,
  clampPageSize,
  listRequest,
} from "./operations";
import type { PageInfo, Row } from "./rows";
import type { ResourceTypeName } from "./resource-types";
import { refineResourceName } from "./resources";

type Filter<TName extends ResourceTypeName> =
  Record<string, unknown>;
type Order<TName extends ResourceTypeName> =
  Record<string, unknown>;

export interface UseResourceListOptions<TName extends ResourceTypeName> {
  fields: readonly string[];
  pageSize?: number;
  /** 1-based page owned by the caller. Use this for URL/router-owned lists. */
  page?: number;
  /** 1-based initial page; the hook then owns the page through its setters. */
  initialPage?: number;
  filter?: Filter<TName>;
  order?: Order<TName>;
  enabled?: boolean;
}

export interface UseResourceListResult {
  rows: readonly Row[];
  /** Total matching rows, owned and reported by the backend. */
  total: number | undefined;
  /** Total pages = ceil(total / pageSize); undefined until `total` is known. */
  pageCount: number | undefined;
  /** 1-based index of the page currently shown. */
  page: number;
  pageSize: number;
  pageInfo: PageInfo | undefined;
  hasNext: boolean;
  hasPrev: boolean;
  /** Jump to any 1-based page (offset pagination); clamped to `[1, pageCount]`. */
  setPage: (page: number) => void;
  firstPage: () => void;
  nextPage: () => void;
  prevPage: () => void;
  lastPage: () => void;
  fetching: boolean;
  error: Error | null;
  refetch: () => void;
}

export function useResourceList<
  TName extends ResourceTypeName = ResourceTypeName,
>(
  modelLabel: string,
  options: UseResourceListOptions<TName>,
): UseResourceListResult {
  const {
    fields,
    pageSize = DEFAULT_PAGE_SIZE,
    initialPage = 1,
    filter,
    order,
    enabled = true,
  } = options;
  const size = clampPageSize(pageSize);
  const modelMetadata = useModelMetadata(modelLabel);
  const resource = modelMetadata?.resource ?? null;
  const resourceName = resource ? refineResourceName(resource) : "__angee_disabled__";
  const active = enabled && Boolean(modelLabel) && Boolean(resource);

  const resetKey = stableJson({
    modelLabel,
    size,
    filter: filter ?? null,
    order: order ?? null,
  });
  const controlledPage = options.page === undefined
    ? undefined
    : normalisePage(options.page);
  const initial = normalisePage(initialPage);
  const [pageState, setPageState] = useState(() => ({
    resetKey,
    initial,
    page: initial,
  }));
  const currentPage = controlledPage
    ?? (pageState.resetKey !== resetKey
      ? 1
      : pageState.initial !== initial
        ? initial
        : pageState.page);

  useEffect(() => {
    if (controlledPage !== undefined) return;
    setPageState((current) => {
      if (current.resetKey !== resetKey) {
        return { resetKey, initial, page: 1 };
      }
      if (current.initial !== initial) {
        return { resetKey, initial, page: initial };
      }
      return current;
    });
  }, [controlledPage, initial, resetKey]);

  const hasuraWhere = useMemo(
    () => hasuraWhereFromAngeeFilter(filter),
    [filter],
  );
  const hasuraOrderBy = useMemo(
    () => hasuraOrderByFromAngeeOrder(order),
    [order],
  );
  const listMeta = useMemo(
    () =>
      resource
        ? listRequest(resource, {
            fields,
            where: hasuraWhere,
            orderBy: hasuraOrderBy,
          }).meta
        : undefined,
    [fields, hasuraOrderBy, hasuraWhere, resource],
  );

  const run = useList<RowRecord, HttpError>({
    resource: resourceName,
    dataProviderName: resource?.schemaName,
    pagination: {
      mode: "server",
      currentPage,
      pageSize: size,
    },
    meta: listMeta,
    queryOptions: {
      enabled: active,
    },
  });

  const rows = useMemo(
    () => (run.result.data ?? []) as readonly Row[],
    [run.result.data],
  );
  const total = run.result.total;
  const pageCount = total === undefined
    ? undefined
    : Math.max(1, Math.ceil(total / size));

  const setPage = useCallback(
    (next: number) => {
      const floored = Math.max(1, Math.floor(next));
      setPageState({
        resetKey,
        initial,
        page: pageCount ? Math.min(floored, pageCount) : floored,
      });
    },
    [initial, pageCount, resetKey],
  );
  const firstPage = useCallback(
    () => setPageState({ resetKey, initial, page: 1 }),
    [initial, resetKey],
  );
  const nextPage = useCallback(
    () =>
      setPageState((current) => {
        const page = current.resetKey === resetKey ? current.page : 1;
        return {
          resetKey,
          initial,
          page: pageCount ? Math.min(page + 1, pageCount) : page + 1,
        };
      }),
    [initial, pageCount, resetKey],
  );
  const prevPage = useCallback(
    () =>
      setPageState((current) => {
        const page = current.resetKey === resetKey ? current.page : 1;
        return { resetKey, initial, page: Math.max(1, page - 1) };
      }),
    [initial, resetKey],
  );
  const lastPage = useCallback(() => {
    if (pageCount) setPageState({ resetKey, initial, page: pageCount });
  }, [initial, pageCount, resetKey]);

  return {
    rows,
    total,
    pageCount,
    page: currentPage,
    pageSize: size,
    pageInfo: undefined,
    hasNext: pageCount !== undefined && currentPage < pageCount,
    hasPrev: currentPage > 1,
    setPage,
    firstPage,
    nextPage,
    prevPage,
    lastPage,
    fetching: run.query.isFetching,
    error: resourceListError(run.query.error),
    refetch: () => {
      void run.query.refetch();
    },
  };
}

type RowRecord = BaseRecord & Row;
type FieldTree = Map<string, FieldTree>;
type UnsupportedFilter = { field: string; operator: string };
type HasuraOrderBy = Record<string, unknown>;

export function refineFieldsFromPaths(paths: readonly string[]): Fields {
  const root: FieldTree = new Map();
  for (const path of paths) {
    addFieldPath(root, path);
  }
  return fieldTreeToFields(root);
}

export function refineSortersFromAngeeOrder(
  order: unknown,
): CrudSorting | undefined {
  if (!isRecord(order)) return undefined;
  const sorters = Object.entries(order).flatMap(([field, direction]) => {
    if (direction === undefined || direction === null) return [];
    return [{
      field,
      order: String(direction).toLowerCase() === "desc" ? "desc" : "asc",
    } as const];
  });
  return sorters.length > 0 ? sorters : undefined;
}

export function hasuraOrderByFromAngeeOrder(
  order: unknown,
): HasuraOrderBy | undefined {
  const sorters = refineSortersFromAngeeOrder(order);
  if (!sorters) return undefined;
  const orderBy: HasuraOrderBy = {};
  for (const sorter of sorters) {
    setNestedOrder(orderBy, sorter.field, sorter.order);
  }
  return Object.keys(orderBy).length > 0 ? orderBy : undefined;
}

export function refineFiltersFromAngeeFilter(
  filter: unknown,
): CrudFilters | undefined {
  const filters = filtersFromRecord(filter);
  return filters.length > 0 ? filters : undefined;
}

export function hasuraWhereFromAngeeFilter(
  filter: unknown,
): Record<string, unknown> | undefined {
  const where = hasuraWhereFromRecord(filter);
  return Object.keys(where).length > 0 ? where : undefined;
}

function addFieldPath(tree: FieldTree, rawPath: string): void {
  const path = rawPath.trim();
  if (!path) return;
  const [head, ...tail] = path.split(".").filter(Boolean);
  if (!head) return;
  if (tail.length === 0) {
    tree.set(head, tree.get(head) ?? new Map());
    return;
  }
  const child = tree.get(head) ?? new Map();
  tree.set(head, child);
  addFieldPath(child, tail.join("."));
}

function fieldTreeToFields(tree: FieldTree): Fields {
  return [...tree.entries()].map(([field, child]) =>
    child.size === 0 ? field : { [field]: fieldTreeToFields(child) },
  );
}

function filtersFromRecord(filter: unknown): CrudFilters {
  if (!isRecord(filter)) return [];
  const filters: CrudFilters = [];
  for (const [field, lookup] of Object.entries(filter)) {
    if (isAndKey(field) || isOrKey(field)) {
      const children = filtersFromBranch(lookup);
      if (children.length > 0) {
        filters.push({
          operator: isOrKey(field) ? "or" : "and",
          value: children,
        });
      }
      continue;
    }
    if (isNotKey(field)) {
      throw new Error(
        "The refine/Hasura list provider does not support Angee NOT filters yet.",
      );
    }
    filters.push(...filtersForLookup(field, lookup));
  }
  return filters;
}

function filtersFromBranch(branch: unknown): CrudFilters {
  const items = Array.isArray(branch) ? branch : [branch];
  return items.flatMap(filtersFromRecord);
}

function hasuraWhereFromRecord(filter: unknown): Record<string, unknown> {
  if (!isRecord(filter)) return {};
  const where: Record<string, unknown> = {};
  for (const [field, lookup] of Object.entries(filter)) {
    if (isAndKey(field) || isOrKey(field)) {
      const children = hasuraWhereFromBranch(lookup);
      if (children.length > 0) {
        where[isOrKey(field) ? "_or" : "_and"] = children;
      }
      continue;
    }
    if (isNotKey(field)) {
      throw new Error(
        "The refine/Hasura list provider does not support Angee NOT filters yet.",
      );
    }
    where[field] = hasuraComparisonForLookup(field, lookup);
  }
  return where;
}

function hasuraWhereFromBranch(branch: unknown): Record<string, unknown>[] {
  const items = Array.isArray(branch) ? branch : [branch];
  return items
    .map(hasuraWhereFromRecord)
    .filter((item) => Object.keys(item).length > 0);
}

function filtersForLookup(field: string, lookup: unknown): CrudFilters {
  if (!isRecord(lookup) || Array.isArray(lookup)) {
    return [{ field, operator: "eq", value: lookup }];
  }

  const filters: CrudFilters = [];
  for (const [operator, value] of Object.entries(lookup)) {
    if (isUnsupportedRefineLookupOperator(operator)) {
      throw unsupportedFilter({ field, operator });
    }
    const refineOperator = lookupOperator(operator);
    if (refineOperator) {
      filters.push({
        field,
        operator: refineOperator.operator,
        value: refineOperator.value === KEEP_VALUE ? value : refineOperator.value,
      });
      continue;
    }
    if (isRecord(value)) {
      filters.push(...filtersForLookup(`${field}.${operator}`, value));
      continue;
    }
    throw unsupportedFilter({ field, operator });
  }
  return filters;
}

function hasuraComparisonForLookup(
  field: string,
  lookup: unknown,
): Record<string, unknown> {
  if (!isRecord(lookup) || Array.isArray(lookup)) {
    return { _eq: lookup };
  }

  const comparison: Record<string, unknown> = {};
  for (const [operator, value] of Object.entries(lookup)) {
    if (isUnsupportedLookupOperator(operator)) {
      throw unsupportedFilter({ field, operator });
    }
    const hasuraOperator = hasuraLookupOperator(operator);
    if (hasuraOperator) {
      comparison[hasuraOperator.operator] =
        hasuraOperator.value === KEEP_VALUE ? value : hasuraOperator.value;
      continue;
    }
    if (isRecord(value)) {
      comparison[operator] = hasuraComparisonForLookup(
        `${field}.${operator}`,
        value,
      );
      continue;
    }
    throw unsupportedFilter({ field, operator });
  }
  return comparison;
}

const KEEP_VALUE = Symbol("keep-value");

function lookupOperator(
  operator: string,
): { operator: LogicalFilter["operator"]; value: unknown | typeof KEEP_VALUE } | null {
  switch (operator) {
    case "exact":
    case "sqid":
    case "pk":
    case "_eq":
      return { operator: "eq", value: KEEP_VALUE };
    case "ne":
    case "_neq":
      return { operator: "ne", value: KEEP_VALUE };
    case "gt":
    case "_gt":
      return { operator: "gt", value: KEEP_VALUE };
    case "gte":
    case "_gte":
      return { operator: "gte", value: KEEP_VALUE };
    case "lt":
    case "_lt":
      return { operator: "lt", value: KEEP_VALUE };
    case "lte":
    case "_lte":
      return { operator: "lte", value: KEEP_VALUE };
    case "inList":
    case "_in":
      return { operator: "in", value: KEEP_VALUE };
    case "_nin":
      return { operator: "nin", value: KEEP_VALUE };
    case "isNull":
    case "_is_null":
      return { operator: "null", value: KEEP_VALUE };
    case "jsonContains":
    case "_contains":
      return null;
    case "contains":
      return { operator: "containss", value: KEEP_VALUE };
    case "iContains":
      return { operator: "contains", value: KEEP_VALUE };
    case "startsWith":
      return { operator: "startswiths", value: KEEP_VALUE };
    case "iStartsWith":
      return { operator: "startswith", value: KEEP_VALUE };
    case "endsWith":
      return { operator: "endswiths", value: KEEP_VALUE };
    case "iEndsWith":
      return { operator: "endswith", value: KEEP_VALUE };
    default:
      return null;
  }
}

function hasuraLookupOperator(
  operator: string,
): { operator: string; value: unknown | typeof KEEP_VALUE } | null {
  switch (operator) {
    case "exact":
    case "sqid":
    case "pk":
    case "_eq":
      return { operator: "_eq", value: KEEP_VALUE };
    case "ne":
    case "_neq":
      return { operator: "_neq", value: KEEP_VALUE };
    case "gt":
    case "_gt":
      return { operator: "_gt", value: KEEP_VALUE };
    case "gte":
    case "_gte":
      return { operator: "_gte", value: KEEP_VALUE };
    case "lt":
    case "_lt":
      return { operator: "_lt", value: KEEP_VALUE };
    case "lte":
    case "_lte":
      return { operator: "_lte", value: KEEP_VALUE };
    case "inList":
    case "_in":
      return { operator: "_in", value: KEEP_VALUE };
    case "_nin":
      return { operator: "_nin", value: KEEP_VALUE };
    case "isNull":
    case "_is_null":
      return { operator: "_is_null", value: KEEP_VALUE };
    case "jsonContains":
    case "_contains":
      return { operator: "_contains", value: KEEP_VALUE };
    case "contains":
      return { operator: "_like", value: KEEP_VALUE };
    case "iContains":
      return { operator: "_ilike", value: KEEP_VALUE };
    default:
      return null;
  }
}

function isUnsupportedLookupOperator(operator: string): boolean {
  return operator === "iExact" || operator === "_ilike" || operator === "_like";
}

function isUnsupportedRefineLookupOperator(operator: string): boolean {
  return (
    operator === "jsonContains" ||
    operator === "_contains" ||
    isUnsupportedLookupOperator(operator)
  );
}

function unsupportedFilter({ field, operator }: UnsupportedFilter): Error {
  return new Error(
    `Unsupported refine/Hasura list filter "${operator}" on field "${field}".`,
  );
}

function isAndKey(value: string): boolean {
  return value === "AND" || value === "and" || value === "_and";
}

function isOrKey(value: string): boolean {
  return value === "OR" || value === "or" || value === "_or";
}

function isNotKey(value: string): boolean {
  return value === "NOT" || value === "not" || value === "_not";
}

function normalisePage(page: number): number {
  return Math.max(1, Math.floor(page));
}

function setNestedOrder(
  target: HasuraOrderBy,
  rawPath: string,
  value: "asc" | "desc",
): void {
  const [head, ...tail] = rawPath.split(".").filter(Boolean);
  if (!head) return;
  if (tail.length === 0) {
    target[head] = value;
    return;
  }
  const child = isRecord(target[head]) ? target[head] : {};
  target[head] = child;
  setNestedOrder(child, tail.join("."), value);
}

function resourceListError(error: HttpError | null): Error | null {
  if (!error) return null;
  if (error instanceof Error) return error;
  return Object.assign(new Error(error.message), error);
}

function stableJson(value: unknown): string {
  return JSON.stringify(sortJson(value));
}

function sortJson(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortJson);
  if (!isRecord(value)) return value;
  return Object.fromEntries(
    Object.entries(value)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => [key, sortJson(item)]),
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
