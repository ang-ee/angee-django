import * as React from "react";
import { flexRender, type Cell as TableCellModel, type Column as TableColumn, type ColumnDef } from "@tanstack/react-table";
import type { AggregateBucket, AggregateMeasureOperator } from "@angee/refine";
import type {
  ModelMetadata,
  Row,
} from "@angee/metadata";
import { isDateField, rowValueAtPath, resourceFieldPathToSnake } from "@angee/metadata";
import { type UiTranslate } from "../../../i18n";
import { enumValueLabel, groupFieldLabel, statusLabel } from "../../../lib/labels";
import { formatNumber } from "../../../lib/format-number";
import { titleCase } from "../../../lib/titleCase";
import { Badge } from "../../../ui/badge";
import { ChipList } from "../../../ui/chip";
import { dateFromUnknown, formatDate, formatDateTime } from "../../../widgets/date-format";
import { canonicalOptionValue } from "../../../widgets/types";
import { RecordReferenceChips } from "../../relation/RecordReference";
import { columnTone } from "../../page";
import type { ColumnAggregate, ColumnDescriptor, PageColumnAlign } from "../../page";
import type { GroupMeasure } from "./types";
export function cellContent<TRow extends Row>(
  column: ColumnDescriptor<TRow>,
  row: TRow,
  t: UiTranslate,
  metadata?: ModelMetadata | null,
): React.ReactNode {
  if (column.render) return column.render(row);
  const queryField = column.queryField;
  const projected = rowValueAtPath(row, queryField?.row?.path ?? column.field);
  if (column.relationList) {
    if (projected == null) return null;
    if (!Array.isArray(projected)) {
      throw new Error(`Relation list column "${column.field}" expected an array.`);
    }
    const { model, identityPath, labelPath, colorPath } = column.relationList;
    return <RecordReferenceChips model={model} records={projected.map((item: unknown) => {
      if (item == null || typeof item !== "object") {
        throw new Error(`Relation list column "${column.field}" expected related records.`);
      }
      const id = rowValueAtPath(item as Row, identityPath);
      const label = rowValueAtPath(item as Row, labelPath);
      if (typeof id !== "string") {
        throw new Error(`Relation list column "${column.field}" expected a related record identity.`);
      }
      const color = colorPath ? rowValueAtPath(item as Row, colorPath) : undefined;
      return { id, label: label == null ? undefined : String(label), color: typeof color === "string" ? color : undefined };
    })} />;
  }
  const labelPath = queryField?.relation?.labelPath;
  const value = labelPath ? rowValueAtPath(row, labelPath) ?? projected : projected;
  const field = queryField ?? metadata?.fields[column.field];
  const enumOptions = field?.kind === "enum"
    ? field.values?.map((item) => ({ value: item.value, label: enumValueLabel(item) }))
    : undefined;
  const enumValue = enumOptions?.find((item) => item.value === canonicalOptionValue(enumOptions, value));
  const tone = columnTone(column, value);
  if (tone) {
    const label = value == null ? "" : String(value);
    return <Badge tone={tone}>{enumValue?.label ?? (label ? statusLabel(label) : "—")}</Badge>;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return "—";
    return <ChipList items={value.map((item, index) => ({ id: `${String(item)}:${index}`, label: String(item) }))} />;
  }
  if (enumValue) return enumValue.label;
  const date = isDateField(field, column.field)
    ? dateFromUnknown(value)
    : null;
  if (date) return <CompactDate value={date} />;
  return displayValue(value, t);
}

function CompactDate({ value }: { value: Date }): React.ReactElement {
  return <time dateTime={value.toISOString()} title={formatDateTime(value)} className="tabular-nums">{formatDate(value)}</time>;
}

export function renderCell<TRow extends Row>(
  cell: TableCellModel<TRow, unknown>,
): React.ReactNode {
  return flexRender(cell.column.columnDef.cell, cell.getContext());
}

export function tableColumnLabel<TRow extends Row>(
  column: TableColumn<TRow, unknown>,
): React.ReactNode {
  return columnMeta(column.columnDef).label ?? column.id;
}

export function ariaSortForColumn<TRow extends Row>(
  column: TableColumn<TRow, unknown>,
): React.AriaAttributes["aria-sort"] {
  const sort = column.getIsSorted();
  return sort === "asc" ? "ascending" : sort === "desc" ? "descending" : "none";
}

export function rowActionLabelForTableColumn<TRow extends Row>(
  column: TableColumn<TRow, unknown>,
  row: TRow,
  t: UiTranslate,
): string {
  const value = rowValueAtPath(row, column.id);
  if (Array.isArray(value)) {
    const label = value.map((item) => String(item)).join(", ").trim();
    return label || t("list.record");
  }
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "boolean") return t(value ? "list.yes" : "list.no");
  return t("list.record");
}

export { rowValueAtPath as readPath } from "@angee/metadata";

export function groupMeasuresFromColumns<TRow extends Row>(
  columns: readonly ColumnDescriptor<TRow>[],
): readonly GroupMeasure[] {
  const measures: GroupMeasure[] = [];
  const seen = new Set<string>();
  for (const column of columns) {
    if (!isMeasureOperator(column.aggregate)) continue;
    const key = `${column.aggregate}:${column.field}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const label = columnLabel(column);
    measures.push({
      op: column.aggregate,
      field: column.field,
      columnId: column.field,
      label,
      unit: "",
    });
  }
  return measures;
}

export function hasuraMeasuresFromGroupMeasures(
  measures: readonly GroupMeasure[],
  metadata: ModelMetadata | null,
): readonly GroupMeasure[] {
  if (measures.length === 0) return measures;
  return measures.map((measure) => {
    const input = hasuraMeasureInput(measure, metadata);
    return input === measure.field
      ? measure
      : { ...measure, field: input, input };
  });
}

function hasuraMeasureInput(
  measure: Pick<GroupMeasure, "op" | "field">,
  metadata: ModelMetadata | null,
): string {
  const snakeField = resourceFieldPathToSnake(measure.field);
  const declared = metadata?.resource?.aggregateMeasures?.find(
    (candidate) =>
      candidate.op === measure.op &&
      (candidate.field === measure.field || candidate.field === snakeField),
  );
  return declared?.input ?? declared?.field ?? snakeField;
}

function isMeasureOperator(
  aggregate: ColumnAggregate | undefined,
): aggregate is AggregateMeasureOperator {
  return (
    aggregate === "count" ||
    aggregate === "sum" ||
    aggregate === "avg" ||
    aggregate === "min" ||
    aggregate === "max"
  );
}

function columnLabel<TRow extends Row>(column: ColumnDescriptor<TRow>): string {
  const header = column.header;
  if (typeof header === "string") return header;
  if (typeof header === "number") return String(header);
  return titleCase(column.field);
}

export function columnLabelText<TRow extends Row>(
  column: ColumnDescriptor<TRow>,
): string {
  const header = column.header;
  if (typeof header === "string") return header;
  if (typeof header === "number") return String(header);
  return groupFieldLabel(column.field);
}

export function measureValue(
  bucket: AggregateBucket,
  measure: Pick<GroupMeasure, "op" | "field">,
): unknown {
  if (measure.op === "count") return bucket.count;
  return bucket[measure.op]?.[measure.field];
}

export function formatMeasure(
  value: unknown,
  measure: Pick<GroupMeasure, "unit">,
): string {
  const formatted = value == null
    || typeof value === "number"
    || typeof value === "bigint"
    || typeof value === "string"
    ? formatNumber(value)
    : String(value);
  return measure.unit ? `${formatted} ${measure.unit}` : formatted;
}

function displayValue(value: unknown, t: UiTranslate): React.ReactNode {
  if (value == null || value === "") return "—";
  if (typeof value === "boolean") return t(value ? "list.yes" : "list.no");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function alignOf<TRow extends Row>(column: ColumnDef<TRow>): PageColumnAlign {
  return columnMeta(column).align ?? "left";
}

export function columnHasInteractiveContent<TRow extends Row>(
  column: ColumnDef<TRow>,
): boolean {
  return columnMeta(column).interactive === true;
}

function columnMeta<TRow extends Row>(
  column: ColumnDef<TRow>,
): {
  align?: PageColumnAlign;
  label?: React.ReactNode;
  field?: string;
  aggregate?: ColumnAggregate;
  queryOnly?: boolean;
  interactive?: boolean;
} {
  return (
    (column.meta as
      | {
          align?: PageColumnAlign;
          label?: React.ReactNode;
          field?: string;
          aggregate?: ColumnAggregate;
          queryOnly?: boolean;
          interactive?: boolean;
        }
      | undefined) ?? {}
  );
}

/** A native accessor column for query behavior outside the declared display columns. */
export function isQueryOnlyColumn<TRow extends Row>(
  column: ColumnDef<TRow>,
): boolean {
  return columnMeta(column).queryOnly === true;
}

/** Keep native grouping/sorting accessors out of rendering and the display chooser. */
export function withQueryOnlyColumnsHidden<TRow extends Row>(
  columns: readonly ColumnDef<TRow>[],
  previous: Record<string, boolean>,
): Record<string, boolean> {
  let next: Record<string, boolean> | null = null;
  for (const column of columns) {
    const id = column.id;
    if (!id || !isQueryOnlyColumn(column)) continue;
    if (previous[id] === false) continue;
    next = next ?? { ...previous };
    next[id] = false;
  }
  return next ?? previous;
}

export function isInteractiveTarget(target: EventTarget): boolean {
  return target instanceof HTMLElement
    && Boolean(
      target.closest(
        "a,button,input,select,textarea,label,[role='button'],[role='menuitem'],[role='checkbox']",
      ),
    );
}

export { enumValueLabel, groupFieldLabel } from "../../../lib/labels";
