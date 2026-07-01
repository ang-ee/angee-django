import * as React from "react";
import {
  DEFAULT_PAGE_SIZE,
  clampPageSize,
} from "@angee/refine";
import type {
  Row,
} from "@angee/resources";
import type { ResourceViewContextValue } from "./resource-view-context";
import type {
  ResourceViewFilter,
  ResourceViewSort,
} from "./resource-view-model";
import type { ColumnDescriptor } from "./page";

export interface LocalRowsQuery {
  filter?: ResourceViewFilter;
  sort?: ResourceViewSort | null;
  page?: number;
  pageSize?: number;
  textFields?: readonly string[];
}

export interface LocalRowsResult<TRow extends Row = Row> {
  rows: readonly TRow[];
  total: number;
  page: number;
  pageSize: number;
  pageCount: number;
  hasNext: boolean;
  hasPrev: boolean;
}

export interface LocalRowsDataSource<TRow extends Row = Row> {
  readonly rows: readonly TRow[];
  query: (query?: LocalRowsQuery) => LocalRowsResult<TRow>;
  filterRows: (
    filter: ResourceViewFilter | undefined,
    options?: { textFields?: readonly string[] },
  ) => readonly TRow[];
  sortRows: (
    rows: readonly TRow[],
    sort: ResourceViewSort | null | undefined,
  ) => readonly TRow[];
}

const ROWS_TEXT_FILTER_KEY = "q";

export function createLocalRowsDataSource<TRow extends Row>(
  rows: readonly TRow[],
): LocalRowsDataSource<TRow> {
  return {
    rows,
    query(query = {}) {
      const filteredRows = localRowsFilter(rows, query.filter, {
        textFields: query.textFields,
      });
      const sortedRows = localRowsSort(filteredRows, query.sort);
      const pageSize = clampPageSize(query.pageSize ?? DEFAULT_PAGE_SIZE);
      const pageCount = Math.max(1, Math.ceil(sortedRows.length / pageSize));
      const page = Math.min(dataQueryPage(query), pageCount);
      const pageRows = sortedRows.slice(
        (page - 1) * pageSize,
        page * pageSize,
      );
      return {
        rows: pageRows,
        total: sortedRows.length,
        page,
        pageSize,
        pageCount,
        hasNext: page < pageCount,
        hasPrev: page > 1,
      };
    },
    filterRows(filter, options = {}) {
      return localRowsFilter(rows, filter, options);
    },
    sortRows: localRowsSort,
  };
}

/**
 * Compute the current in-browser page from a local rows source: derive the
 * text-search fields from the columns, run the source query against the
 * resource-view's sort/page/page-size plus the caller's ``filter``, and clamp
 * the URL page back into range when it drifts past the new page count (e.g. a
 * filter shrinks the result set). The shared owner for the two local-rows
 * surfaces (client row-model and in-memory rows); each builds its own source —
 * the page math, search-field derivation, and clamp effect live here. ``filter``
 * is explicit so a surface can merge in its own facets (client) or pass the bare
 * view filter (rows) without this hook inspecting the source.
 */
export function useLocalRowsPage<TRow extends Row>({
  source,
  columns,
  resourceView,
  filter,
}: {
  source: LocalRowsDataSource<TRow>;
  columns: readonly ColumnDescriptor<TRow>[];
  resourceView: ResourceViewContextValue;
  filter: ResourceViewFilter | undefined;
}): LocalRowsResult<TRow> {
  const textFields = React.useMemo(
    () => columns.map((column) => column.field),
    [columns],
  );
  const localPage = React.useMemo(
    () =>
      source.query({
        filter,
        sort: resourceView.state.sort,
        page: resourceView.state.page,
        pageSize: resourceView.state.pageSize,
        textFields,
      }),
    [
      source,
      filter,
      resourceView.state.page,
      resourceView.state.pageSize,
      resourceView.state.sort,
      textFields,
    ],
  );

  React.useEffect(() => {
    if (resourceView.state.page > localPage.pageCount) {
      resourceView.setPage(localPage.pageCount);
    }
  }, [resourceView.setPage, resourceView.state.page, localPage.pageCount]);

  return localPage;
}

export function rowTextFilterValue(filter: ResourceViewFilter): string {
  const value = filter[ROWS_TEXT_FILTER_KEY];
  return typeof value === "string" ? value : "";
}

export function nextRowTextFilter(
  filter: ResourceViewFilter,
  value: string,
): ResourceViewFilter {
  const next = { ...filter };
  const trimmed = value.trim();
  if (trimmed) next[ROWS_TEXT_FILTER_KEY] = trimmed;
  else delete next[ROWS_TEXT_FILTER_KEY];
  return next;
}

export function localRowsFilter<TRow extends Row>(
  rows: readonly TRow[],
  filter: ResourceViewFilter | undefined,
  { textFields = [] }: { textFields?: readonly string[] } = {},
): readonly TRow[] {
  if (!filter || Object.keys(filter).length === 0) return rows;
  const text = rowTextFilterValue(filter).trim().toLowerCase();
  const entries = Object.entries(filter).filter(
    ([field]) => field !== ROWS_TEXT_FILTER_KEY,
  );
  if (!text && entries.length === 0) return rows;
  return rows.filter((row) => {
    if (text && !localRowMatchesText(row, textFields, text)) return false;
    return rowMatchesFilterEntries(row, entries);
  });
}

export function localRowsSort<TRow extends Row>(
  rows: readonly TRow[],
  sort: ResourceViewSort | null | undefined,
): readonly TRow[] {
  if (!sort) return rows;
  const direction = sort.dir === "asc" ? 1 : -1;
  return [...rows].sort((left, right) =>
    compareLocalValues(readPath(left, sort.field), readPath(right, sort.field))
    * direction,
  );
}

export function readPath(value: unknown, path: string): unknown {
  let current = value;
  for (const segment of path.split(".").filter(Boolean)) {
    if (!current || typeof current !== "object" || Array.isArray(current)) {
      return undefined;
    }
    current = (current as Record<string, unknown>)[segment];
  }
  return current;
}

function localRowMatchesText(
  row: Row,
  textFields: readonly string[],
  text: string,
): boolean {
  return textFields.some((field) =>
    String(readPath(row, field) ?? "")
      .toLowerCase()
      .includes(text),
  );
}

function rowMatchesFilterEntries(
  row: Row,
  entries: readonly [string, unknown][],
): boolean {
  return entries.every(([field, lookup]) => {
    if (field === "AND") return rowMatchesBranch(row, lookup, "AND");
    if (field === "OR") return rowMatchesBranch(row, lookup, "OR");
    if (field === "NOT") return !rowMatchesBranch(row, lookup, "AND");
    return matchesLocalLookup(readPath(row, field), lookup);
  });
}

function rowMatchesBranch(
  row: Row,
  branch: unknown,
  operator: "AND" | "OR",
): boolean {
  const filters = Array.isArray(branch) ? branch : [branch];
  const matches = filters.map((filter) =>
    isFilterObject(filter) && rowMatchesFilterEntries(row, Object.entries(filter)),
  );
  return operator === "AND"
    ? matches.every(Boolean)
    : matches.some(Boolean);
}

function matchesLocalLookup(value: unknown, lookup: unknown): boolean {
  if (!lookup || typeof lookup !== "object" || Array.isArray(lookup)) {
    return value === lookup;
  }
  const record = lookup as Record<string, unknown>;
  if ("sqid" in record) return relationPublicId(value) === record.sqid;
  if ("pk" in record) return relationPublicId(value) === record.pk;
  if ("exact" in record) return value === record.exact;
  if (Array.isArray(record.inList)) return record.inList.includes(value);
  if (typeof record.isNull === "boolean") return (value == null) === record.isNull;
  if ("iExact" in record) {
    return String(value ?? "").toLowerCase()
      === String(record.iExact ?? "").toLowerCase();
  }
  if ("contains" in record) {
    return String(value ?? "").includes(String(record.contains ?? ""));
  }
  if (typeof record.iContains === "string") {
    return String(value ?? "")
      .toLowerCase()
      .includes(record.iContains.toLowerCase());
  }
  if ("startsWith" in record) {
    return String(value ?? "").startsWith(String(record.startsWith ?? ""));
  }
  if ("iStartsWith" in record) {
    return String(value ?? "")
      .toLowerCase()
      .startsWith(String(record.iStartsWith ?? "").toLowerCase());
  }
  if ("endsWith" in record) {
    return String(value ?? "").endsWith(String(record.endsWith ?? ""));
  }
  if ("iEndsWith" in record) {
    return String(value ?? "")
      .toLowerCase()
      .endsWith(String(record.iEndsWith ?? "").toLowerCase());
  }
  if ("gt" in record && compareLocalValues(value, record.gt) <= 0) return false;
  if ("gte" in record && compareLocalValues(value, record.gte) < 0) return false;
  if ("lt" in record && compareLocalValues(value, record.lt) >= 0) return false;
  if ("lte" in record && compareLocalValues(value, record.lte) > 0) return false;
  return true;
}

function relationPublicId(value: unknown): unknown {
  if (!value || typeof value !== "object" || Array.isArray(value)) return value;
  const record = value as Record<string, unknown>;
  return record.sqid ?? record.id ?? record.pk ?? value;
}

function compareLocalValues(left: unknown, right: unknown): number {
  if (left == null && right == null) return 0;
  if (left == null) return -1;
  if (right == null) return 1;
  if (typeof left === "number" && typeof right === "number") {
    return left - right;
  }
  if (typeof left === "boolean" && typeof right === "boolean") {
    return Number(left) - Number(right);
  }
  return String(left).localeCompare(String(right), undefined, {
    numeric: true,
    sensitivity: "base",
  });
}

function isFilterObject(value: unknown): value is ResourceViewFilter {
  return Boolean(value)
    && typeof value === "object"
    && !Array.isArray(value)
    && Object.getPrototypeOf(value) === Object.prototype;
}

function dataQueryPage(query: Pick<LocalRowsQuery, "page">): number {
  const page = Math.floor(query.page ?? 1);
  return Number.isFinite(page) ? Math.max(1, page) : 1;
}
