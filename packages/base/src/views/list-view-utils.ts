import type { ReactNode } from "react";
import type { Row } from "@angee/sdk";

import type {
  DataToolbarCustomFilter,
  DataToolbarCustomFilterChip,
  DataToolbarFilterField,
  DataToolbarFilterOption,
  DataToolbarGroupOption,
} from "../toolbars";
import {
  DEFAULT_TEXT_FILTER_FIELD,
  Filter,
  type DataViewFilter,
  type DataViewGroup,
  type DataViewLookup,
  type DataViewLookupOperator,
} from "./data-view-model";
import {
  groupFieldLabel,
  looksLikeDateField,
  readPath,
  statusLabel,
} from "./ListInternals";
import type { ColumnDescriptor } from "./page";

export function buildGroupOptions<TRow extends Row>(
  columns: readonly ColumnDescriptor<TRow>[],
  defaultGroup: DataViewGroup | null | undefined,
): readonly DataToolbarGroupOption[] {
  const options: DataToolbarGroupOption[] = [];
  const seen = new Set<string>();
  const addOption = (option: DataToolbarGroupOption) => {
    if (seen.has(option.id)) return;
    seen.add(option.id);
    options.push(option);
  };

  if (defaultGroup) {
    addOption({
      id: defaultGroup.field,
      label: groupFieldLabel(defaultGroup.field),
      group: defaultGroup,
      type: looksLikeDateField(defaultGroup.field) ? "date" : "value",
    });
  }

  for (const column of columns) {
    if (looksLikeDateField(column.field)) {
      addOption({
        id: column.field,
        label: groupFieldLabel(column.field),
        group: { field: column.field, granularity: "day" },
        type: "date",
      });
      continue;
    }
    if (supportsChoiceFacet(column)) {
      addOption({
        id: column.field,
        label: column.header ?? groupFieldLabel(column.field),
        group: { field: column.field },
        type: "value",
      });
    }
  }

  return options;
}

export function buildFilterOptions<TRow extends Row>(
  columns: readonly ColumnDescriptor<TRow>[],
  rows: readonly TRow[],
): readonly DataToolbarFilterOption[] {
  return columns.flatMap((column) => {
    if (!supportsChoiceFacet(column)) return [];
    return statusValues(column, rows).map((value) => ({
      id: `${column.field}:${value}`,
      label: statusLabel(value),
      chipLabel: statusLabel(value),
      filter: { [column.field]: { exact: value } },
    }));
  });
}

export function buildFilterFields<TRow extends Row>(
  columns: readonly ColumnDescriptor<TRow>[],
  rows: readonly TRow[],
): readonly DataToolbarFilterField[] {
  const fields: DataToolbarFilterField[] = [];
  for (const column of columns) {
    if (column.field === DEFAULT_TEXT_FILTER_FIELD) {
      fields.push({
        id: column.field,
        field: column.field,
        label: column.header ?? groupFieldLabel(column.field),
        type: "text",
      });
      continue;
    }
    if (looksLikeDateField(column.field)) {
      fields.push({
        id: column.field,
        field: column.field,
        label: column.header ?? groupFieldLabel(column.field),
        type: "datetime",
      });
      continue;
    }
    if (supportsChoiceFacet(column)) {
      fields.push({
        id: column.field,
        field: column.field,
        label: column.header ?? groupFieldLabel(column.field),
        type: "selection",
        options: statusValues(column, rows).map((value) => ({
          value,
          label: statusLabel(value),
        })),
      });
    }
  }
  return fields;
}

function statusValues<TRow extends Row>(
  column: ColumnDescriptor<TRow>,
  rows: readonly TRow[],
): string[] {
  if (column.tone) {
    const toneValues = Object.keys(column.tone).filter(
      (key) => key === key.toUpperCase(),
    );
    if (toneValues.length > 0) return toneValues;
  }
  const values = new Set<string>();
  for (const row of rows) {
    const value = readPath(row, column.field);
    if (typeof value === "string" && value.trim()) values.add(value);
  }
  return [...values].sort((left, right) => left.localeCompare(right));
}

export function supportsChoiceFacet<TRow extends Row>(
  column: ColumnDescriptor<TRow>,
): boolean {
  if (column.tone) return true;
  // TODO: derive facet/group fields from addon/schema choices, not a
  // hardcoded lifecycle status field.
  return column.field === "status";
}

export function activeFilterIdsFor(
  filter: DataViewFilter,
  options: readonly DataToolbarFilterOption[],
): readonly string[] {
  const value = Filter.from(filter);
  return options.flatMap((option) => {
    const facet = Filter.facetFromFilter(option.filter);
    if (!facet) return [];
    return value.facetValues(facet.field).includes(facet.value)
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
  const facet = option ? Filter.facetFromFilter(option.filter) : null;
  if (!facet) return filter;
  return Filter.from(filter).toggleFacet(facet);
}

export function textFilterValue(filter: DataViewFilter): string {
  return Filter.from(filter).textTerm();
}

export function nextTextFilter(filter: DataViewFilter, value: string): DataViewFilter {
  return Filter.from(filter).withTextTerm(value);
}

export function customFilterChipsFor(
  filter: DataViewFilter,
  filterOptions: readonly DataToolbarFilterOption[],
  fields: readonly DataToolbarFilterField[],
): readonly DataToolbarCustomFilterChip[] {
  const chips: DataToolbarCustomFilterChip[] = [];
  const fieldLabels = new Map(
    fields.map((field) => [field.field ?? field.id, field.label]),
  );
  for (const [field, value] of Object.entries(filter)) {
    if (!isLookup(value)) continue;
    for (const [operator, operatorValue] of Object.entries(value)) {
      if (!isLookupOperator(operator)) continue;
      if (isFacetFilter(field, operator, operatorValue, filterOptions)) continue;
      if (field === DEFAULT_TEXT_FILTER_FIELD && operator === "iContains") {
        continue;
      }
      chips.push({
        id: customFilterId(field, operator),
        label: customFilterChipLabel({
          fieldLabel: fieldLabels.get(field) ?? groupFieldLabel(field),
          operator,
          value: operatorValue,
        }),
      });
    }
  }
  return chips;
}

export function addCustomFilter(
  filter: DataViewFilter,
  customFilter: DataToolbarCustomFilter,
): DataViewFilter {
  const next = { ...filter };
  const current = isLookup(next[customFilter.field])
    ? { ...(next[customFilter.field] as DataViewLookup) }
    : {};
  if (customFilter.operator === "isNotNull") {
    current.isNull = false;
  } else if (customFilter.operator === "isNull") {
    current.isNull = true;
  } else {
    current[customFilter.operator] = customFilter.value ?? null;
  }
  next[customFilter.field] = current;
  return next;
}

export function removeCustomFilter(
  filter: DataViewFilter,
  id: string,
): DataViewFilter {
  const [field, operator] = parseCustomFilterId(id);
  if (!field || !operator || !isLookupOperator(operator)) return filter;
  const current = filter[field];
  if (!isLookup(current)) return filter;
  const nextLookup = { ...current };
  delete nextLookup[operator];
  const next = { ...filter };
  if (Object.keys(nextLookup).length === 0) delete next[field];
  else next[field] = nextLookup;
  return next;
}

export function mergeFilterOptions(
  explicit: readonly DataToolbarFilterOption[] | undefined,
  inferred: readonly DataToolbarFilterOption[],
): readonly DataToolbarFilterOption[] {
  return mergeById(explicit, inferred);
}

export function mergeFilterFields(
  explicit: readonly DataToolbarFilterField[] | undefined,
  inferred: readonly DataToolbarFilterField[],
): readonly DataToolbarFilterField[] {
  return mergeById(explicit, inferred);
}

export function createLabelForModel(model: string): string {
  const name = model.split(".").at(-1) ?? "record";
  return `New ${groupFieldLabel(name).toLowerCase()}`;
}

function mergeById<TOption extends { id: string }>(
  explicit: readonly TOption[] | undefined,
  inferred: readonly TOption[],
): readonly TOption[] {
  const merged: TOption[] = [];
  const seen = new Set<string>();
  for (const option of [...(explicit ?? []), ...inferred]) {
    if (seen.has(option.id)) continue;
    seen.add(option.id);
    merged.push(option);
  }
  return merged;
}

function isFacetFilter(
  field: string,
  operator: DataViewLookupOperator,
  value: unknown,
  options: readonly DataToolbarFilterOption[],
): boolean {
  if (operator !== "exact" && operator !== "inList") return false;
  const facets = options
    .map((option) => Filter.facetFromFilter(option.filter))
    .filter((facet): facet is { field: string; value: string } => facet !== null)
    .filter((facet) => facet.field === field);
  if (facets.length === 0) return false;
  if (operator === "exact") {
    return facets.some((facet) => facet.value === value);
  }
  return Array.isArray(value)
    && value.every((item) => facets.some((facet) => facet.value === item));
}

function customFilterChipLabel({
  fieldLabel,
  operator,
  value,
}: {
  fieldLabel: ReactNode;
  operator: DataViewLookupOperator;
  value: unknown;
}): ReactNode {
  if (operator === "isNull") {
    return `${labelText(fieldLabel) ?? "Field"} is ${
      value === false ? "not empty" : "empty"
    }`;
  }
  return `${labelText(fieldLabel) ?? "Field"} ${operatorLabel(operator)} ${
    filterValueLabel(value)
  }`;
}

function operatorLabel(operator: DataViewLookupOperator): string {
  switch (operator) {
    case "exact":
    case "iExact":
      return "is";
    case "inList":
      return "is one of";
    case "isNull":
      return "is";
    case "contains":
    case "iContains":
      return "contains";
    case "startsWith":
    case "iStartsWith":
      return "starts with";
    case "endsWith":
    case "iEndsWith":
      return "ends with";
    case "gt":
      return ">";
    case "gte":
      return ">=";
    case "lt":
      return "<";
    case "lte":
      return "<=";
  }
}

function filterValueLabel(value: unknown): string {
  if (Array.isArray(value)) return value.map(String).join(", ");
  return String(value ?? "");
}

function customFilterId(field: string, operator: DataViewLookupOperator): string {
  return `${encodeURIComponent(field)}:${operator}`;
}

function parseCustomFilterId(
  id: string,
): readonly [string | null, string | null] {
  const [field, operator, extra] = id.split(":");
  if (!field || !operator || extra !== undefined) return [null, null];
  return [decodeURIComponent(field), operator];
}

function isLookup(value: unknown): value is DataViewLookup {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isLookupOperator(value: string): value is DataViewLookupOperator {
  return [
    "exact",
    "inList",
    "isNull",
    "iExact",
    "contains",
    "iContains",
    "startsWith",
    "iStartsWith",
    "endsWith",
    "iEndsWith",
    "gt",
    "gte",
    "lt",
    "lte",
  ].includes(value);
}

function labelText(value: ReactNode): string | null {
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  return null;
}
