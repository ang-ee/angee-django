import type { ReactNode } from "react";
import { filterFieldType as metadataFilterFieldType, isDateField, supportsChoiceFacet as metadataSupportsChoiceFacet, type ResourceQuery, type ModelFieldMetadata, type ModelMetadata, type Row } from "@angee/metadata";
import { queryForColumns } from "../resource-query";
import { statusLabel } from "../../../lib/labels";
import type { ResourceToolbarFilterField, ResourceToolbarFilterOption } from "../../../toolbars";
import { DEFAULT_TEXT_FILTER_FIELD } from "../resource-view-model";
import { readPath } from "../resource-view-list-body";
import type { ColumnDescriptor } from "../../page";
import { fieldLabel } from "../model-metadata-defaults";
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
  suppliedQuery?: ResourceQuery,
): readonly ResourceToolbarFilterField[] {
  const query = suppliedQuery ?? queryForColumns(columns, metadata);
  const fields: ResourceToolbarFilterField[] = [];
  const seen = new Set<string>();
  const addField = (
    fieldName: string,
    column: ColumnDescriptor<TRow> | undefined,
  ) => {
    const capability = query.fields[fieldName]?.filter;
    if (seen.has(fieldName) || !capability?.operators.length) {
      return;
    }
    const field = metadata?.fields[fieldName];
    const filterType = capability.values.length ? "selection" : filterFieldType(fieldName, column, field ?? query.fields[fieldName]);
    const operators = [...capability.operators, ...(capability.operators.includes("isNull") ? ["isNotNull" as const] : [])];
    if (!filterType) return;
    seen.add(fieldName);
    if (filterType === "selection") {
      const options = capability.values.map(({ value, description }) => ({ value, label: description ?? statusLabel(value) }));
      fields.push({
        id: fieldName,
        field: fieldName,
        label: fieldLabel(fieldName, field, column?.header),
        type: "selection",
        operators,
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
      operators,
    });
  };
  for (const column of columns) {
    addField(column.field, column);
  }
  for (const fieldName of Object.keys(query.fields)) {
    addField(fieldName, undefined);
  }
  return fields;
}

function filterFieldType<TRow extends Row>(
  fieldName: string,
  column: ColumnDescriptor<TRow> | undefined,
  field: Parameters<typeof metadataFilterFieldType>[1],
): ResourceToolbarFilterField["type"] | null {
  if (fieldName === DEFAULT_TEXT_FILTER_FIELD) return "text";
  return metadataFilterFieldType(fieldName, field, {
    hasOptions: Boolean(column?.options?.length),
    hasTone: Boolean(column?.tone),
    allowStatusFallback: Boolean(column),
  });
}

export function dateGroupType(
  fieldName: string,
  field: ModelFieldMetadata | undefined,
): boolean {
  return isDateField(field, fieldName);
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
