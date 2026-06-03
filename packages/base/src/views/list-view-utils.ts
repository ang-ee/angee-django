import type { Row } from "@angee/sdk";

import type { DataToolbarFilterOption } from "../toolbars";
import type { DataViewFilter } from "./data-view-model";
import {
  groupFieldLabel,
  readPath,
  statusLabel,
} from "./list-internals";
import type { ColumnDescriptor } from "./page";

export function buildFilterOptions<TRow extends Row>(
  columns: readonly ColumnDescriptor<TRow>[],
  rows: readonly TRow[],
): readonly DataToolbarFilterOption[] {
  return columns.flatMap((column) => {
    if (column.field !== "status" && !column.tone) return [];
    return statusValues(column, rows).map((value) => ({
      id: `${column.field}:${value}`,
      label: statusLabel(value),
      chipLabel: statusLabel(value),
      filter: { [column.field]: { exact: value } },
    }));
  });
}

function statusValues<TRow extends Row>(
  column: ColumnDescriptor<TRow>,
  rows: readonly TRow[],
): string[] {
  const values = new Set<string>();
  if (column.tone) {
    for (const key of Object.keys(column.tone)) {
      if (key === key.toUpperCase()) values.add(key);
    }
  }
  if (values.size === 0) {
    for (const row of rows) {
      const value = readPath(row, column.field);
      if (typeof value === "string" && value.trim()) values.add(value);
    }
  }
  return [...values].sort(compareStatusValue);
}

const STATUS_ORDER = ["DRAFT", "IN_REVIEW", "ACTIVE", "ARCHIVED"];

function compareStatusValue(left: string, right: string): number {
  const leftIndex = STATUS_ORDER.indexOf(left.toUpperCase());
  const rightIndex = STATUS_ORDER.indexOf(right.toUpperCase());
  if (leftIndex !== -1 || rightIndex !== -1) {
    return (leftIndex === -1 ? Number.MAX_SAFE_INTEGER : leftIndex)
      - (rightIndex === -1 ? Number.MAX_SAFE_INTEGER : rightIndex);
  }
  return left.localeCompare(right);
}

export function activeFilterIdsFor(
  filter: DataViewFilter,
  options: readonly DataToolbarFilterOption[],
): readonly string[] {
  return options.flatMap((option) => {
    const facet = facetFilter(option);
    if (!facet) return [];
    return statusFilterValues(filter, facet.field).includes(facet.value)
      ? [option.id]
      : [];
  });
}

export function nextFacetFilter(
  filter: DataViewFilter,
  options: readonly DataToolbarFilterOption[],
  id: string,
): DataViewFilter {
  const option = options.find((candidate) => candidate.id === id);
  const facet = option ? facetFilter(option) : null;
  if (!facet) return filter;
  const current = statusFilterValues(filter, facet.field);
  const nextValues = current.includes(facet.value)
    ? current.filter((value) => value !== facet.value)
    : [...current, facet.value];
  const next = { ...filter };
  if (nextValues.length === 0) {
    delete next[facet.field];
  } else if (nextValues.length === 1) {
    next[facet.field] = { exact: nextValues[0] };
  } else {
    next[facet.field] = { inList: nextValues };
  }
  return next;
}

function facetFilter(
  option: DataToolbarFilterOption,
): { field: string; value: string } | null {
  const entry = Object.entries(option.filter)[0];
  if (!entry) return null;
  const [field, lookup] = entry;
  if (!field || !lookup || typeof lookup !== "object" || Array.isArray(lookup)) {
    return null;
  }
  const exact = (lookup as Record<string, unknown>).exact;
  return typeof exact === "string" ? { field, value: exact } : null;
}

function statusFilterValues(filter: DataViewFilter, field: string): readonly string[] {
  const lookup = filter[field];
  if (!lookup || typeof lookup !== "object" || Array.isArray(lookup)) return [];
  const exact = (lookup as Record<string, unknown>).exact;
  if (typeof exact === "string") return [exact];
  const inList = (lookup as Record<string, unknown>).inList;
  return Array.isArray(inList)
    ? inList.filter((value): value is string => typeof value === "string")
    : [];
}

export function textFilterValue(filter: DataViewFilter): string {
  const title = filter.title;
  if (!title || typeof title !== "object" || Array.isArray(title)) return "";
  const value = (title as Record<string, unknown>).iContains;
  return typeof value === "string" ? value : "";
}

export function nextTextFilter(filter: DataViewFilter, value: string): DataViewFilter {
  const next = { ...filter };
  const trimmed = value.trim();
  if (trimmed) next.title = { iContains: trimmed };
  else delete next.title;
  return next;
}

export function createLabelForModel(model: string): string {
  const name = model.split(".").at(-1) ?? "record";
  return `New ${groupFieldLabel(name).toLowerCase()}`;
}
