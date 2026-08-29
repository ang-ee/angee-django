import type { ReactNode } from "react";
import { filterFieldType as metadataFilterFieldType, isDateField, supportsChoiceFacet as metadataSupportsChoiceFacet, type ModelFieldMetadata, type ModelMetadata, type Row } from "@angee/metadata";
import { statusLabel } from "../../../lib/labels";
import type { ResourceToolbarFilterField, ResourceToolbarFilterOption } from "../../../toolbars";
import { DEFAULT_TEXT_FILTER_FIELD } from "../resource-view-model";
import { readPath } from "../resource-view-list-body";
import type { ColumnDescriptor } from "../../page";
import { enumOptions, fieldLabel } from "../model-metadata-defaults";
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
