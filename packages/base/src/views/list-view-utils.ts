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
    const resolvedGroup = resolveDataViewGroup(defaultGroup, metadata);
    if (!groupAllowedByDataQuery(resolvedGroup, metadata)) continue;
    const field = metadata?.fields[resolvedGroup.field];
    const type = dateGroupType(resolvedGroup.field, field) ? "date" : "value";
    addOption({
      id: resolvedGroup.field,
      label: relationGroupLabel(resolvedGroup, metadata)
        ?? groupLabel(resolvedGroup.field, field),
      group: resolvedGroup,
      type,
      ...(type === "date" ? { granularities: DATE_GROUP_GRANULARITIES } : {}),
    });
  }

  const dataQueryGroupByFields = metadata?.dataQuery?.groupByFields ?? [];
  const groupAliases = metadata?.dataQuery?.groupAliases ?? [];
  const aliasedAggregateFields = new Set(
    groupAliases.map((alias) => alias.aggregateField),
  );
  for (const alias of groupAliases) {
    if (!metadata) continue;
    const group = groupAliasToDataViewGroup(alias);
    if (!groupAllowedByDataQuery(group, metadata)) continue;
    const field = metadata.fields[alias.field];
    if (!field) continue;
    const type = dateGroupType(alias.field, field) ? "date" : "value";
    addOption({
      id: alias.field,
      label: groupLabel(alias.field, field),
      group,
      type,
      ...(type === "date" ? { granularities: DATE_GROUP_GRANULARITIES } : {}),
    });
  }
  for (const fieldName of dataQueryGroupByFields) {
    if (!metadata) continue;
    if (aliasedAggregateFields.has(fieldName)) continue;
    const field = metadata.fields[fieldName];
    if (!field) continue;
    if (field.kind === "relation") continue;
    const type = dateGroupType(fieldName, field) ? "date" : "value";
    addOption({
      id: fieldName,
      label: groupLabel(fieldName, field),
      group: {
        field: fieldName,
        ...(type === "date" ? { granularity: "day" as const } : {}),
      },
      type,
      ...(type === "date" ? { granularities: DATE_GROUP_GRANULARITIES } : {}),
    });
  }

  for (const column of columns) {
    const field = metadata?.fields[column.field];
    const relationGroup = relationGroupOptionForColumn(column, metadata);
    if (relationGroup && groupAllowedByDataQuery(relationGroup.group, metadata)) {
      addOption(relationGroup);
      continue;
    }
    if (!groupAllowedByDataQuery({ field: column.field }, metadata)) continue;
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

function relationGroupOptionForColumn<TRow extends Row>(
  column: ColumnDescriptor<TRow>,
  metadata: ModelMetadata | null,
): DataToolbarGroupOption | null {
  const group = relationGroupForFieldPath(column.field, metadata);
  if (!group) return null;
  const [relationField] = column.field.split(".");
  const field = relationField ? metadata?.fields[relationField] : undefined;
  return {
    id: column.field,
    label: fieldLabel(relationField ?? column.field, field, column.header),
    group,
    type: "value",
  };
}

function relationGroupForFieldPath(
  fieldPath: string,
  metadata: ModelMetadata | null,
): DataViewGroup | null {
  const [relationField, labelField, ...rest] = fieldPath.split(".");
  if (!relationField || !labelField || rest.length > 0) return null;
  const field = metadata?.fields[relationField];
  const filter = field?.relationFilter;
  if (field?.kind !== "relation" || !filter?.aggregateKey) return null;
  return {
    field: fieldPath,
    aggregateField: filter.field,
    aggregateKey: filter.aggregateKey,
  };
}

function relationGroupLabel(
  group: DataViewGroup,
  metadata: ModelMetadata | null,
): ReactNode | null {
  if (!group.aggregateField) return null;
  const [relationField, labelField, ...rest] = group.field.split(".");
  if (!relationField || !labelField || rest.length > 0) return null;
  const field = metadata?.fields[relationField];
  return field?.kind === "relation" ? fieldLabel(relationField, field) : null;
}

export function resolveDataViewGroup(
  group: DataViewGroup,
  metadata: ModelMetadata | null,
): DataViewGroup {
  if (group.aggregateField && group.aggregateKey) return group;
  const aliasGroup = groupAliasForField(group.field, metadata);
  if (aliasGroup) return { ...group, ...aliasGroup };
  const relationGroup = relationGroupForFieldPath(group.field, metadata);
  return relationGroup ? { ...group, ...relationGroup } : group;
}

function groupAliasForField(
  field: string,
  metadata: ModelMetadata | null,
): DataViewGroup | null {
  const alias = metadata?.dataQuery?.groupAliases?.find((item) => item.field === field);
  return alias ? groupAliasToDataViewGroup(alias) : null;
}

function groupAliasToDataViewGroup(alias: {
  field: string;
  aggregateField: string;
  aggregateKey: string;
}): DataViewGroup {
  return {
    field: alias.field,
    aggregateField: alias.aggregateField,
    aggregateKey: alias.aggregateKey,
  };
}

function groupAllowedByDataQuery(
  group: DataViewGroup,
  metadata: ModelMetadata | null,
): boolean {
  const groupByFields = metadata?.dataQuery?.groupByFields;
  if (!groupByFields) return true;
  const aggregateField = group.aggregateField ?? group.field;
  return groupByFields.includes(aggregateField) || groupByFields.includes(group.field);
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
  const columnsByField = new Map(columns.map((column) => [column.field, column]));
  return fields.flatMap((filterField) => {
    if (filterField?.type !== "selection") return [];
    const field = filterField.field ?? filterField.id;
    const column = columnsByField.get(field);
    const options = column
      ? selectionOptions(column, rows, filterField)
      : filterField.options ?? [];
    return options.map((option) => ({
      id: `${field}:${option.value}`,
      label: option.label,
      chipLabel: option.label,
      filter: { [field]: { exact: option.value } },
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
  const seen = new Set<string>();
  const addField = (
    fieldName: string,
    column: ColumnDescriptor<TRow> | undefined,
  ) => {
    if (seen.has(fieldName) || !filterAllowedByDataQuery(fieldName, metadata)) {
      return;
    }
    const field = metadata?.fields[fieldName];
    const filterType = filterFieldType(fieldName, column, field);
    if (!filterType) return;
    seen.add(fieldName);
    if (filterType === "selection") {
      const options = enumOptions(field);
      fields.push({
        id: fieldName,
        field: fieldName,
        label: fieldLabel(fieldName, field, column?.header),
        type: "selection",
        options: options.length > 0
          ? options
          : column
            ? statusValues(column, rows).map((value) => ({
                value,
                label: statusLabel(value),
              }))
            : [],
      });
      return;
    }
    fields.push({
      id: fieldName,
      field: fieldName,
      label: fieldLabel(fieldName, field, column?.header),
      type: filterType,
    });
  };
  for (const column of columns) {
    addField(column.field, column);
  }
  for (const fieldName of metadata?.dataQuery?.filterFields ?? []) {
    addField(fieldName, undefined);
  }
  return fields;
}

function filterFieldType<TRow extends Row>(
  fieldName: string,
  column: ColumnDescriptor<TRow> | undefined,
  field: ModelFieldMetadata | undefined,
): DataToolbarFilterField["type"] | null {
  if (field?.kind === "enum") return "selection";
  if (field?.kind === "scalar" && field.scalar === "String") return "text";
  if (field?.kind === "scalar" && field.scalar === "Boolean") return "boolean";
  if (field?.kind === "scalar" && (field.scalar === "Int" || field.scalar === "Float")) return "number";
  if (field?.kind === "scalar" && field.scalar === "DateTime") return "datetime";
  if (field?.kind === "scalar" && field.scalar === "Date") return "date";
  if (fieldName === DEFAULT_TEXT_FILTER_FIELD) return "text";
  if (looksLikeDateField(fieldName)) return "datetime";
  if (column && supportsChoiceFacet(column, null)) return "selection";
  return null;
}

function filterAllowedByDataQuery(
  fieldName: string,
  metadata: ModelMetadata | null,
): boolean {
  const filterFields = metadata?.dataQuery?.filterFields;
  return !filterFields || filterFields.includes(fieldName);
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

/**
 * The field the free-text search box reads/writes. Defaults to the model's
 * ``recordRepresentation`` (e.g. ``displayName`` for Person) so search filters the
 * model's real title field, falling back to the generic ``title`` when unknown.
 */
export function resolveTextFilterField(
  metadata: { recordRepresentation?: string } | null | undefined,
): string {
  return metadata?.recordRepresentation ?? DEFAULT_TEXT_FILTER_FIELD;
}

export function textFilterValue(
  filter: DataViewFilter,
  field: string = DEFAULT_TEXT_FILTER_FIELD,
): string {
  return Filter.from(filter).textTerm(field);
}

export function nextTextFilter(
  filter: DataViewFilter,
  value: string,
  field: string = DEFAULT_TEXT_FILTER_FIELD,
): DataViewFilter {
  return Filter.from(filter).withTextTerm(value, field);
}

export function customFilterChipsFor(
  filter: DataViewFilter,
  filterOptions: readonly DataToolbarFilterOption[],
  fields: readonly DataToolbarFilterField[],
  textField: string = DEFAULT_TEXT_FILTER_FIELD,
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
      // The free-text search term owns its own input, so it is not a removable chip.
      if (field === textField && operator === "iContains") {
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
