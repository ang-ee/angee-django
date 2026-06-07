import type { ReactNode } from "react";
import type {
  ModelFieldMetadata,
  ModelMetadata,
} from "@angee/sdk";

import type { WidgetOption } from "../widgets";
import type {
  ColumnDescriptor,
  FieldDescriptor,
} from "./page";
import { titleCase } from "../lib/titleCase";
import { groupFieldLabel } from "./ListInternals";

const ENUM_OPTION_WIDGETS = new Set([
  "select",
  "selection",
  "statusbar",
  "statusBadge",
  "ribbon",
]);

/** Apply SDL-derived column labels and enum options without overriding props. */
export function columnsWithMetadataDefaults<TRow extends object>(
  columns: readonly ColumnDescriptor<TRow>[],
  metadata: ModelMetadata | null,
): readonly ColumnDescriptor<TRow>[] {
  return columns.map((column) => {
    const field = metadata?.fields[column.field];
    const options = enumOptions(field);
    return {
      ...column,
      header: fieldLabel(column.field, field, column.header),
      ...(column.options === undefined
        && isEnumOptionWidget(column.widget)
        && options.length > 0
        ? { options }
        : {}),
    };
  });
}

/** Apply SDL-derived field labels and enum options without overriding props. */
export function fieldsWithMetadataDefaults(
  fields: readonly FieldDescriptor[],
  metadata: ModelMetadata | null,
): readonly FieldDescriptor[] {
  return fields.map((field) => {
    const fieldMetadata = metadata?.fields[field.name];
    const options = enumOptions(fieldMetadata);
    return {
      ...field,
      label: fieldLabel(field.name, fieldMetadata, field.label),
      ...(field.options === undefined
        && isEnumOptionWidget(field.widget ?? field.kind)
        && options.length > 0
        ? { options }
        : {}),
    };
  });
}

/** Resolve a field label from explicit props, SDL metadata, then title-case. */
export function fieldLabel(
  name: string,
  metadata: ModelFieldMetadata | undefined,
  explicit?: ReactNode,
): ReactNode {
  return explicit ?? metadata?.label ?? titleCase(name);
}

/** Resolve a group label from SDL metadata, then group-specific field text. */
export function groupLabel(
  name: string,
  metadata: ModelFieldMetadata | undefined,
): string {
  return metadata?.label ?? groupFieldLabel(name);
}

/** Return enum widget options for a metadata field, or an empty list. */
export function enumOptions(
  field: ModelFieldMetadata | undefined,
): readonly WidgetOption[] {
  if (field?.kind !== "enum" && field?.kind !== "list") return [];
  return field.values?.map((value) => ({
    value: value.value,
    label: value.label,
  })) ?? [];
}

function isEnumOptionWidget(widget: string | undefined): boolean {
  return widget !== undefined && ENUM_OPTION_WIDGETS.has(widget);
}
