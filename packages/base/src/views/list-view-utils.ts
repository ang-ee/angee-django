import type { ReactNode } from "react";
import type { ModelFieldMetadata, ModelMetadata, Row } from "@angee/sdk";

import { dedupeBy } from "../lib/dedupe";
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
  isLookupOperator,
  type DataViewFilter,
  type DataViewGroup,
  type DataViewLookup,
  type DataViewLookupOperator,
  type FilterFacet,
} from "./data-view-model";
import {
  groupFieldLabel,
  looksLikeDateField,
  readPath,
  statusLabel,
} from "./ListInternals";
import type { ColumnDescriptor } from "./page";
import {
  enumOptions,
  fieldLabel,
  groupLabel,
} from "./model-metadata-defaults";

const DATE_GROUP_GRANULARITIES = ["year", "quarter", "month", "week", "day"] as const;

export function buildGroupOptions<TRow extends Row>(
  columns: readonly ColumnDescriptor<TRow>[],
  metadata: ModelMetadata | null,
  defaultGroups: DataViewGroup | readonly DataViewGroup[] | null | undefined,
): readonly DataToolbarGroupOption[] {
  const options: DataToolbarGroupOption[] = [];
  const seen = new Set<string>();
  const addOption = (option: DataToolbarGroupOption) => {
    if (seen.has(option.id)) return;
    seen.add(option.id);
    options.push(option);
  };

  for (const defaultGroup of defaultGroupList(defaultGroups)) {
    const field = metadata?.fields[defaultGroup.field];
    const type = dateGroupType(defaultGroup.field, field) ? "date" : "value";
    addOption({
      id: defaultGroup.field,
      label: groupLabel(defaultGroup.field, field),
      group: defaultGroup,
      type,
      ...(type === "date" ? { granularities: DATE_GROUP_GRANULARITIES } : {}),
    });
  }

  for (const column of columns) {
    const field = metadata?.fields[column.field];
    if (dateGroupType(column.field, field)) {
      addOption({
        id: column.field,
        // Group labels are field-derived (groupFieldLabel trims a trailing
        // "At"), independent of the column's display header.
        label: groupLabel(column.field, field),
        group: { field: column.field, granularity: "day" },
        type: "date",
        granularities: DATE_GROUP_GRANULARITIES,
      });
      continue;
    }
    if (supportsChoiceFacet(column, metadata)) {
      addOption({
        id: column.field,
        label: groupLabel(column.field, field),
        group: { field: column.field },
        type: "value",
      });
    }
  }

  return options;
}

function defaultGroupList(
  defaultGroups: DataViewGroup | readonly DataViewGroup[] | null | undefined,
): readonly DataViewGroup[] {
  if (!defaultGroups) return [];
  return isDataViewGroupList(defaultGroups) ? defaultGroups : [defaultGroups];
}

function isDataViewGroupList(
  value: DataViewGroup | readonly DataViewGroup[],
): value is readonly DataViewGroup[] {
  return Array.isArray(value);
}

export function buildFilterOptions<TRow extends Row>(
  columns: readonly ColumnDescriptor<TRow>[],
  rows: readonly TRow[],
  fields: readonly DataToolbarFilterField[],
): readonly DataToolbarFilterOption[] {
  const filterFields = new Map(fields.map((field) => [field.field ?? field.id, field]));
  return columns.flatMap((column) => {
    const filterField = filterFields.get(column.field);
    if (filterField?.type !== "selection") return [];
    return selectionOptions(column, rows, filterField).map((option) => ({
      id: `${column.field}:${option.value}`,
      label: option.label,
      chipLabel: option.label,
      filter: { [column.field]: { exact: option.value } },
    }));
  });
}

function selectionOptions<TRow extends Row>(
  column: ColumnDescriptor<TRow>,
  rows: readonly TRow[],
  field: DataToolbarFilterField,
): readonly { value: string; label: ReactNode }[] {
  if (field.options && field.options.length > 0) return field.options;
  return statusValues(column, rows).map((value) => ({
    value,
    label: statusLabel(value),
  }));
}

export function buildFilterFields<TRow extends Row>(
  columns: readonly ColumnDescriptor<TRow>[],
  rows: readonly TRow[],
  metadata: ModelMetadata | null,
): readonly DataToolbarFilterField[] {
  const fields: DataToolbarFilterField[] = [];
  for (const column of columns) {
    const field = metadata?.fields[column.field];
    const filterType = filterFieldType(column, field);
    if (!filterType) continue;
    if (filterType === "selection") {
      const options = enumOptions(field);
      fields.push({
        id: column.field,
        field: column.field,
        label: fieldLabel(column.field, field, column.header),
        type: "selection",
        options: options.length > 0
          ? options
          : statusValues(column, rows).map((value) => ({
              value,
              label: statusLabel(value),
            })),
      });
      continue;
    }
    fields.push({
      id: column.field,
      field: column.field,
      label: fieldLabel(column.field, field, column.header),
      type: filterType,
    });
  }
  return fields;
}

function filterFieldType<TRow extends Row>(
  column: ColumnDescriptor<TRow>,
  field: ModelFieldMetadata | undefined,
): DataToolbarFilterField["type"] | null {
  if (field?.kind === "enum") return "selection";
  if (field?.kind === "scalar" && field.scalar === "String") return "text";
  if (field?.kind === "scalar" && field.scalar === "DateTime") return "datetime";
  if (field?.kind === "scalar" && field.scalar === "Date") return "date";
  if (column.field === DEFAULT_TEXT_FILTER_FIELD) return "text";
  if (looksLikeDateField(column.field)) return "datetime";
  if (supportsChoiceFacet(column, null)) return "selection";
  return null;
}

function dateGroupType(
  fieldName: string,
  field: ModelFieldMetadata | undefined,
): boolean {
  if (field?.kind === "scalar") {
    return field.scalar === "DateTime" || field.scalar === "Date";
  }
  return looksLikeDateField(fieldName);
}

export function supportsChoiceFacet<TRow extends Row>(
  column: ColumnDescriptor<TRow>,
  metadata: ModelMetadata | null,
): boolean {
  const field = metadata?.fields[column.field];
  if (field?.kind === "enum") return true;
  if (column.options && column.options.length > 0) return true;
  if (column.tone) return true;
  // No-metadata escape hatch for RowsListView's built-in status facet.
  return column.field === "status";
}

function statusValues<TRow extends Row>(
  column: ColumnDescriptor<TRow>,
  rows: readonly TRow[],
): string[] {
  if (column.options && column.options.length > 0) {
    return column.options.map((option) => option.value);
  }
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

export function activeFilterIdsFor(
  filter: DataViewFilter,
  options: readonly DataToolbarFilterOption[],
): readonly string[] {
  const value = Filter.from(filter);
  return options.flatMap((option) => {
    const facet = Filter.facetFromFilter(option.filter);
    if (!facet) return [];
    return value.facetValues(facet).includes(facet.value)
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
          fieldLabel: fieldLabel(field, undefined, fieldLabels.get(field)),
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

export function mergeGroupOptions(
  explicit: readonly DataToolbarGroupOption[] | undefined,
  inferred: readonly DataToolbarGroupOption[],
): readonly DataToolbarGroupOption[] {
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
  return dedupeBy([...(explicit ?? []), ...inferred], (option) => option.id);
}

function isFacetFilter(
  field: string,
  operator: DataViewLookupOperator,
  value: unknown,
  options: readonly DataToolbarFilterOption[],
): boolean {
  const facets = options
    .map((option) => Filter.facetFromFilter(option.filter))
    .filter((facet): facet is FilterFacet => facet !== null)
    .filter((facet) => facet.field === field);
  if (facets.length === 0) return false;
  if (operator === "inList") {
    return Array.isArray(value)
      && value.every((item) => facets.some((facet) => facet.value === item));
  }
  const operatorFacets = facets.filter(
    (facet) => (facet.lookup ?? "exact") === operator,
  );
  if (operatorFacets.length === 0) return false;
  return operatorFacets.some((facet) => facet.value === value);
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

function labelText(value: ReactNode): string | null {
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  return null;
}
