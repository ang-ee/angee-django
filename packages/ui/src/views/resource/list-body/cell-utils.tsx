import * as React from "react";
import { flexRender, type Cell as TableCellModel, type Column as TableColumn, type ColumnDef } from "@tanstack/react-table";
import type { AggregateBucket, AggregateMeasureOperator } from "@angee/refine";
import type { ModelEnumValueMetadata, ModelMetadata, Row } from "@angee/metadata";
import { isDateField, rowValueAtPath, resourceFieldPathToSnake } from "@angee/metadata";
import { type UiTranslate } from "../../../i18n";
import { RelativeTime } from "../../../fragments/RelativeTime";
import { statusLabel } from "../../../lib/labels";
import { titleCase } from "../../../lib/titleCase";
import { Badge } from "../../../ui/badge";
import { Chip } from "../../../ui/chip";
import { dateFromUnknown } from "../../../widgets/date-format";
import type { ResourceViewContextValue } from "../resource-view-context";
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
  const value = readPath(row, column.field);
  const tone = columnTone(column, value);
  if (tone) {
    const label = value == null ? "" : String(value);
    return <Badge tone={tone}>{label ? statusLabel(label) : "-"}</Badge>;
  }
  if (Array.isArray(value)) {
    return (
      <span className="inline-flex min-w-0 flex-wrap items-center gap-1">
        {value.map((item, index) => (
          <Chip key={`${String(item)}:${index}`} tone="info" size="sm">
            {String(item)}
          </Chip>
        ))}
      </span>
    );
  }
  const field = metadata?.fields[column.field];
  const date = isDateField(field, column.field)
    ? dateFromUnknown(value)
    : null;
  if (date) return <RelativeTime value={date} />;
  return displayValue(value, t);
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
  resourceView: ResourceViewContextValue,
): React.AriaAttributes["aria-sort"] {
  const field = columnMeta(column.columnDef).field ?? column.id;
  if (resourceView.state.sort?.field !== field) return "none";
  return resourceView.state.sort.dir === "asc" ? "ascending" : "descending";
}

export function rowActionLabelForTableColumn<TRow extends Row>(
  column: TableColumn<TRow, unknown>,
  row: TRow,
  t: UiTranslate,
): string {
  const value = readPath(row, column.id);
  if (Array.isArray(value)) {
    const label = value.map((item) => String(item)).join(", ").trim();
    return label || t("list.record");
  }
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "boolean") return t(value ? "list.yes" : "list.no");
  return t("list.record");
}

export function readPath(row: Row, path: string): unknown {
  return rowValueAtPath(row, path);
}

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
  const formatted = formatMeasureValue(value);
  return measure.unit ? `${formatted} ${measure.unit}` : formatted;
}

function formatMeasureValue(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value.toLocaleString();
  }
  if (typeof value === "bigint") return value.toLocaleString();
  if (typeof value === "string" && /^-?\d+$/.test(value)) {
    return BigInt(value).toLocaleString();
  }
  return value == null ? "" : String(value);
}

function displayValue(value: unknown, t: UiTranslate): React.ReactNode {
  if (value == null) return "";
  if (typeof value === "boolean") return t(value ? "list.yes" : "list.no");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function alignOf<TRow extends Row>(column: ColumnDef<TRow>): PageColumnAlign {
  return columnMeta(column).align ?? "left";
}

function columnMeta<TRow extends Row>(
  column: ColumnDef<TRow>,
): {
  align?: PageColumnAlign;
  label?: React.ReactNode;
  field?: string;
  aggregate?: ColumnAggregate;
  groupingOnly?: boolean;
} {
  return (
    column.meta as
      | {
          align?: PageColumnAlign;
          label?: React.ReactNode;
          field?: string;
          aggregate?: ColumnAggregate;
          groupingOnly?: boolean;
        }
      | undefined
  ) ?? {};
}

/** A grouping-accessor column that exists only to feed TanStack grouping. */
export function isGroupingOnlyColumn<TRow extends Row>(
  column: ColumnDef<TRow>,
): boolean {
  return columnMeta(column).groupingOnly === true;
}

/** Merge visibility=false for every grouping-only column into ``previous``.

    A grouping accessor exists only to feed TanStack grouping; without this the
    axis renders as an empty data column with a raw-path header beside the
    declared columns. Returns ``previous`` unchanged when nothing new appeared. */
export function withGroupingOnlyColumnsHidden<TRow extends Row>(
  columns: readonly ColumnDef<TRow>[],
  previous: Record<string, boolean>,
): Record<string, boolean> {
  let next: Record<string, boolean> | null = null;
  for (const column of columns) {
    const id = column.id;
    if (!id || !isGroupingOnlyColumn(column)) continue;
    if (previous[id] === false) continue;
    next = next ?? { ...previous };
    next[id] = false;
  }
  return next ?? previous;
}

export function nextSort(
  current: ResourceViewContextValue["state"]["sort"],
  field: string,
): ResourceViewContextValue["state"]["sort"] {
  if (current?.field !== field) return { field, dir: "asc" };
  if (current.dir === "asc") return { field, dir: "desc" };
  return null;
}

export function isInteractiveTarget(target: EventTarget): boolean {
  return target instanceof HTMLElement
    && Boolean(
      target.closest(
        "a,button,input,select,textarea,label,[role='button'],[role='menuitem'],[role='checkbox']",
      ),
    );
}

export function groupFieldLabel(field: string): string {
  const label = titleCase(field);
  return label.endsWith(" At") ? label.slice(0, -3) : label;
}

/**
 * The display label for an enum metadata value: its authored description where
 * the resource artifact provides one, otherwise the humanized value.
 */
export function enumValueLabel(value: ModelEnumValueMetadata): string {
  return value.description ?? statusLabel(value.value);
}
