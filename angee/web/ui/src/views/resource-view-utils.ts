import type { ReactNode } from "react";
import {
  filterFieldType as metadataFilterFieldType,
  groupAllowedByResource,
  groupSupportedByResource,
  isClientRowModel,
  looksLikeDateField,
  resourceGroupDimensionForField,
  supportsChoiceFacet as metadataSupportsChoiceFacet,
  type ModelFieldMetadata,
  type ModelMetadata,
  type Row,
} from "@angee/metadata";

import { dedupeBy } from "../lib/dedupe";
import { statusLabel } from "../lib/labels";
import type {
  ResourceToolbarCustomFilter,
  ResourceToolbarCustomFilterOperator,
  ResourceToolbarCustomFilterChip,
  ResourceToolbarFilterField,
  ResourceToolbarFilterOption,
  ResourceToolbarGroupOption,
} from "../toolbars";
import {
  DEFAULT_TEXT_FILTER_FIELD,
  Filter,
  RESOURCE_VIEW_GROUP_GRANULARITIES,
  isLookupOperator,
  type ResourceViewFilter,
  type ResourceViewGroup,
  type ResourceViewLookup,
  type ResourceViewLookupOperator,
  type FilterFacet,
} from "./resource-view-model";
import {
  groupFieldLabel,
  readPath,
} from "./resource-view-list-body";
import type { ColumnDescriptor } from "./page";
import {
  enumOptions,
  fieldLabel,
  resourceFieldGroupLabel,
} from "./model-metadata-defaults";

export function buildGroupOptions<TRow extends Row>(
  columns: readonly ColumnDescriptor<TRow>[],
  metadata: ModelMetadata | null,
  defaultGroups: ResourceViewGroup | readonly ResourceViewGroup[] | null | undefined,
): readonly ResourceToolbarGroupOption[] {
  const options: ResourceToolbarGroupOption[] = [];
  const seen = new Set<string>();
  const addOption = (option: ResourceToolbarGroupOption) => {
    if (seen.has(option.id)) return;
    seen.add(option.id);
    options.push(option);
  };

  for (const defaultGroup of defaultGroupList(defaultGroups)) {
    const resolvedGroup = resolveResourceViewGroup(defaultGroup, metadata);
    if (!groupAllowedByResource(resolvedGroup, metadata)) continue;
    const field = metadata?.fields[resolvedGroup.field];
    const type = dateGroupType(resolvedGroup.field, field) ? "date" : "value";
    addOption({
      id: resolvedGroup.field,
      label: relationGroupLabel(resolvedGroup, metadata)
        ?? resourceFieldGroupLabel(resolvedGroup.field, field),
      group: resolvedGroup,
      type,
      ...(type === "date" ? { granularities: RESOURCE_VIEW_GROUP_GRANULARITIES } : {}),
    });
  }

  const resourceGroupByFields = metadata?.resource?.groupByFields ?? [];
  const groupAliases = metadata?.resource?.groupAliases ?? [];
  const aliasedAggregateFields = new Set(
    groupAliases.map((alias) => alias.aggregateField),
  );
  for (const alias of groupAliases) {
    if (!metadata) continue;
    const group = groupAliasToResourceViewGroup(alias);
    if (!groupAllowedByResource(group, metadata)) continue;
    const field = metadata.fields[alias.field];
    if (!field) continue;
    const type = dateGroupType(alias.field, field) ? "date" : "value";
    addOption({
      id: alias.field,
      label: resourceFieldGroupLabel(alias.field, field),
      group,
      type,
      ...(type === "date" ? { granularities: RESOURCE_VIEW_GROUP_GRANULARITIES } : {}),
    });
  }
  for (const fieldName of resourceGroupByFields) {
    if (!metadata) continue;
    if (aliasedAggregateFields.has(fieldName)) continue;
    const field = metadata.fields[fieldName];
    const dimension = resourceGroupDimensionForField(fieldName, metadata);
    if (!field && !dimension) continue;
    if (field?.kind === "relation") continue;
    const type = dateGroupType(fieldName, field) ? "date" : "value";
    addOption({
      id: fieldName,
      label: resourceFieldGroupLabel(fieldName, field),
      group: {
        field: fieldName,
        ...(type === "date" ? { granularity: "day" as const } : {}),
      },
      type,
      ...(type === "date" ? { granularities: RESOURCE_VIEW_GROUP_GRANULARITIES } : {}),
    });
  }

  for (const column of columns) {
    const field = metadata?.fields[column.field];
    const relationGroup = relationGroupOptionForColumn(column, metadata);
    if (relationGroup && groupAllowedByResource(relationGroup.group, metadata)) {
      addOption(relationGroup);
      continue;
    }
    if (!groupAllowedByResource({ field: column.field }, metadata)) continue;
    if (dateGroupType(column.field, field)) {
      addOption({
        id: column.field,
        // Group labels are field-derived (groupFieldLabel trims a trailing
        // "At"), independent of the column's display header.
        label: resourceFieldGroupLabel(column.field, field),
        group: { field: column.field, granularity: "day" },
        type: "date",
        granularities: RESOURCE_VIEW_GROUP_GRANULARITIES,
      });
      continue;
    }
    if (supportsChoiceFacet(column, metadata)) {
      addOption({
        id: column.field,
        label: resourceFieldGroupLabel(column.field, field),
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
): ResourceToolbarGroupOption | null {
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
): ResourceViewGroup | null {
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
  group: ResourceViewGroup,
  metadata: ModelMetadata | null,
): ReactNode | null {
  if (!group.aggregateField) return null;
  const [relationField, labelField, ...rest] = group.field.split(".");
  if (!relationField || !labelField || rest.length > 0) return null;
  const field = metadata?.fields[relationField];
  return field?.kind === "relation" ? fieldLabel(relationField, field) : null;
}

export function resolveResourceViewGroup(
  group: ResourceViewGroup,
  metadata: ModelMetadata | null,
): ResourceViewGroup {
  if (group.aggregateField && group.aggregateKey) {
    return canonicalResourceViewGroup(group, metadata);
  }
  const aliasGroup = groupAliasForField(group.field, metadata);
  if (aliasGroup) return canonicalResourceViewGroup({ ...group, ...aliasGroup }, metadata);
  const relationGroup = relationGroupForFieldPath(group.field, metadata);
  return canonicalResourceViewGroup(
    relationGroup ? { ...group, ...relationGroup } : group,
    metadata,
  );
}

export function validResourceViewGroupStack(
  groupStack: readonly ResourceViewGroup[],
  metadata: ModelMetadata | null,
): readonly ResourceViewGroup[] {
  return groupStack.flatMap((group) => {
    const resolvedGroup = resolveResourceViewGroup(group, metadata);
    return groupSupportedByResource(resolvedGroup, metadata)
      ? [resolvedGroup]
      : [];
  });
}

function groupAliasForField(
  field: string,
  metadata: ModelMetadata | null,
): ResourceViewGroup | null {
  const alias = metadata?.resource?.groupAliases?.find((item) => item.field === field);
  return alias ? groupAliasToResourceViewGroup(alias) : null;
}

function groupAliasToResourceViewGroup(alias: {
  field: string;
  aggregateField: string;
  aggregateKey: string;
}): ResourceViewGroup {
  return {
    field: alias.field,
    aggregateField: alias.aggregateField,
    aggregateKey: alias.aggregateKey,
  };
}

function canonicalResourceViewGroup(
  group: ResourceViewGroup,
  metadata: ModelMetadata | null,
): ResourceViewGroup {
  const dimension = resourceGroupDimensionForField(
    group.aggregateField ?? group.field,
    metadata,
  );
  if (!dimension) return group;
  if (group.aggregateField) {
    return dimension.field === group.aggregateField
      ? group
      : { ...group, aggregateField: dimension.field };
  }
  return dimension.field === group.field ? group : { ...group, field: dimension.field };
}

function defaultGroupList(
  defaultGroups: ResourceViewGroup | readonly ResourceViewGroup[] | null | undefined,
): readonly ResourceViewGroup[] {
  if (!defaultGroups) return [];
  return isResourceViewGroupList(defaultGroups) ? defaultGroups : [defaultGroups];
}

function isResourceViewGroupList(
  value: ResourceViewGroup | readonly ResourceViewGroup[],
): value is readonly ResourceViewGroup[] {
  return Array.isArray(value);
}

export function buildFilterOptions<TRow extends Row>(
  columns: readonly ColumnDescriptor<TRow>[],
  rows: readonly TRow[],
  fields: readonly ResourceToolbarFilterField[],
): readonly ResourceToolbarFilterOption[] {
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
  field: ResourceToolbarFilterField,
): readonly { value: string; label: ReactNode }[] {
  if (field.options) return field.options;
  return statusValues(column, rows).map((value) => ({
    value,
    label: statusLabel(value),
  }));
}

export function buildFilterFields<TRow extends Row>(
  columns: readonly ColumnDescriptor<TRow>[],
  rows: readonly TRow[],
  metadata: ModelMetadata | null,
): readonly ResourceToolbarFilterField[] {
  const fields: ResourceToolbarFilterField[] = [];
  const seen = new Set<string>();
  const addField = (
    fieldName: string,
    column: ColumnDescriptor<TRow> | undefined,
  ) => {
    if (seen.has(fieldName) || !filterAllowedByResource(fieldName, metadata)) {
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
          : metadata === null && column
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
  for (const fieldName of metadata?.resource?.filterFields ?? []) {
    addField(fieldName, undefined);
  }
  return fields;
}

function filterFieldType<TRow extends Row>(
  fieldName: string,
  column: ColumnDescriptor<TRow> | undefined,
  field: ModelFieldMetadata | undefined,
): ResourceToolbarFilterField["type"] | null {
  if (fieldName === DEFAULT_TEXT_FILTER_FIELD) return "text";
  return metadataFilterFieldType(fieldName, field, {
    hasOptions: Boolean(column?.options?.length),
    hasTone: Boolean(column?.tone),
    allowStatusFallback: Boolean(column),
  });
}

function filterAllowedByResource(
  fieldName: string,
  metadata: ModelMetadata | null,
): boolean {
  const filterFields = metadata?.resource?.filterFields;
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
  return metadataSupportsChoiceFacet({
    fieldName: column.field,
    field,
    hasOptions: Boolean(column.options?.length),
    hasTone: Boolean(column.tone),
    // No-metadata escape hatch for RowsListView's built-in status facet.
    allowStatusFallback: metadata === null,
  });
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
  filter: ResourceViewFilter,
  options: readonly ResourceToolbarFilterOption[],
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
  filter: ResourceViewFilter,
  options: readonly ResourceToolbarFilterOption[],
  id: string,
): ResourceViewFilter {
  const option = options.find((candidate) => candidate.id === id);
  const facet = option ? Filter.facetFromFilter(option.filter) : null;
  if (!facet) return filter;
  return Filter.from(filter).toggleFacet(facet);
}

/**
 * The field the free-text search box reads/writes — the model's title field
 * (``recordRepresentation``, e.g. ``display_name``), falling back to the generic
 * ``title`` when unknown.
 *
 * A **server** resource sends the search term as a Hasura ``where`` on this
 * field, so it must be one the resource declares filterable; otherwise the query
 * is rejected (the resource simply never declared its title field filterable). A
 * **client** row model filters in-memory, so any field is fine. When the title
 * field is not server-filterable, fall back to the first filterable text field
 * so free-text search degrades to a working field instead of 500-ing.
 */
export function resolveTextFilterField(
  metadata: ModelMetadata | null | undefined,
): string {
  const rep = metadata?.recordRepresentation ?? DEFAULT_TEXT_FILTER_FIELD;
  const resource = metadata?.resource;
  if (!resource || isClientRowModel(resource) || resource.filterFields.includes(rep)) {
    return rep;
  }
  const fields = metadata?.fields ?? {};
  const fallback = resource.filterFields.find(
    (name) => fields[name]?.kind === "scalar" && fields[name]?.scalar === "String",
  );
  return fallback ?? rep;
}

export function textFilterValue(
  filter: ResourceViewFilter,
  field: string = DEFAULT_TEXT_FILTER_FIELD,
): string {
  return Filter.from(filter).textTerm(field);
}

export function nextTextFilter(
  filter: ResourceViewFilter,
  value: string,
  field: string = DEFAULT_TEXT_FILTER_FIELD,
): ResourceViewFilter {
  return Filter.from(filter).withTextTerm(value, field);
}

export function customFilterChipsFor(
  filter: ResourceViewFilter,
  filterOptions: readonly ResourceToolbarFilterOption[],
  fields: readonly ResourceToolbarFilterField[],
  textField: string = DEFAULT_TEXT_FILTER_FIELD,
): readonly ResourceToolbarCustomFilterChip[] {
  const chips: ResourceToolbarCustomFilterChip[] = [];
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
  filter: ResourceViewFilter,
  customFilter: ResourceToolbarCustomFilter,
): ResourceViewFilter {
  const next = { ...filter };
  const current = isLookup(next[customFilter.field])
    ? { ...(next[customFilter.field] as ResourceViewLookup) }
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
  filter: ResourceViewFilter,
  id: string,
): ResourceViewFilter {
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
  explicit: readonly ResourceToolbarFilterOption[] | undefined,
  inferred: readonly ResourceToolbarFilterOption[],
): readonly ResourceToolbarFilterOption[] {
  return mergeById(explicit, inferred);
}

export function mergeGroupOptions(
  explicit: readonly ResourceToolbarGroupOption[] | undefined,
  inferred: readonly ResourceToolbarGroupOption[],
): readonly ResourceToolbarGroupOption[] {
  return mergeById(explicit, inferred);
}

export function mergeFilterFields(
  explicit: readonly ResourceToolbarFilterField[] | undefined,
  inferred: readonly ResourceToolbarFilterField[],
): readonly ResourceToolbarFilterField[] {
  return mergeById(explicit, inferred);
}

export function createLabelForResource(resource: string): string {
  const name = resource.split(".").at(-1) ?? "record";
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
  operator: ResourceViewLookupOperator,
  value: unknown,
  options: readonly ResourceToolbarFilterOption[],
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
  operator: ResourceViewLookupOperator;
  value: unknown;
}): ReactNode {
  if (operator === "isNull") {
    return `${labelText(fieldLabel) ?? "Field"} is ${
      value === false ? "not empty" : "empty"
    }`;
  }
  return `${labelText(fieldLabel) ?? "Field"} ${filterOperatorLabel(operator)} ${
    filterValueLabel(value)
  }`;
}

export function filterOperatorLabel(
  operator: ResourceViewLookupOperator | ResourceToolbarCustomFilterOperator,
): string {
  switch (operator) {
    case "exact":
      return "is";
    case "inList":
      return "is one of";
    case "isNull":
      return "is empty";
    case "isNotNull":
      return "is not empty";
    case "iContains":
      return "contains";
    case "contains":
      return "contains (case-sensitive)";
    case "iStartsWith":
      return "starts with";
    case "startsWith":
      return "starts with (case-sensitive)";
    case "iEndsWith":
      return "ends with";
    case "endsWith":
      return "ends with (case-sensitive)";
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

function customFilterId(field: string, operator: ResourceViewLookupOperator): string {
  return `${encodeURIComponent(field)}:${operator}`;
}

function parseCustomFilterId(
  id: string,
): readonly [string | null, string | null] {
  const [field, operator, extra] = id.split(":");
  if (!field || !operator || extra !== undefined) return [null, null];
  return [decodeURIComponent(field), operator];
}

function isLookup(value: unknown): value is ResourceViewLookup {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function labelText(value: ReactNode): string | null {
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  return null;
}
