import * as React from "react";
import { type Row as TableRowModel } from "@tanstack/react-table";
import type { AggregateBucket } from "@angee/refine";
import { ResourceQuery, type GroupAxis, type ModelMetadata, type Row } from "@angee/metadata";
import { format } from "date-fns";
import { Glyph } from "../../../chrome/Glyph";
import { useUiT, type UiTranslate } from "../../../i18n";
import { TableCell, TableRow } from "../../../ui/table";
import type { ResourceViewGroup } from "../resource-view-model";
import { enumValueLabel } from "./cell-utils";
import { queryForColumns } from "../resource-query";
import type { ColumnDescriptor } from "../../page";
export function GroupHeader<TRow extends Row>({
  row,
  colSpan,
  groupStack,
}: {
  row: TableRowModel<TRow>;
  colSpan: number;
  groupStack: readonly ResourceViewGroup[];
}): React.ReactElement {
  const t = useUiT();
  const canExpand = row.getCanExpand();
  const expanded = row.getIsExpanded();
  const label = groupedRowLabel(row, groupStack, t("list.emptyValue"), t);
  const rowCount = row.getLeafRows().length;
  const indent = { paddingLeft: `calc(0.75rem + ${row.depth * 1.25}rem)` };
  // The chevron only appears when the header is a toggle; the lead/trailing
  // content is identical either way, so it is rendered once and the branch
  // chooses only the wrapper (interactive button vs static row).
  const content = (
    <>
      <span className="inline-flex min-w-0 items-center gap-2 font-semibold text-fg">
        {canExpand ? (
          <Glyph
            name={expanded ? "chevron-down" : "chevron-right"}
            className="size-3.5 shrink-0 text-fg-muted"
          />
        ) : null}
        <span className="min-w-0 truncate">{label}</span>
        <span className="font-normal text-fg-muted">
          {rowCount.toLocaleString()}
        </span>
      </span>
    </>
  );
  return (
    <TableRow>
      <TableCell colSpan={colSpan} className="h-8 bg-sheet-2 p-0">
        {canExpand ? (
          // aria-controls is omitted deliberately: the group's rows are loose
          // virtualized siblings with no stable container id to reference.
          <button
            type="button"
            className="flex h-8 w-full min-w-0 items-center justify-between gap-3 px-3 text-left text-13 outline-none hover:bg-inset focus-visible:focus-ring"
            style={indent}
            aria-expanded={expanded}
            onClick={() => row.toggleExpanded()}
          >
            {content}
          </button>
        ) : (
          <div
            className="flex h-8 items-center justify-between gap-3 px-3 text-13"
            style={indent}
          >
            {content}
          </div>
        )}
      </TableCell>
    </TableRow>
  );
}

/** Resolve declared grouping through the same semantic owner for resource and local rows. */
export function tableGroupAxes<TRow extends object>(
  groups: readonly ResourceViewGroup[],
  metadata: ModelMetadata | null | undefined,
  columns: readonly ColumnDescriptor<TRow>[] = [],
  suppliedQuery?: ResourceQuery,
): readonly GroupAxis[] {
  const query = suppliedQuery ?? queryForColumns(columns, metadata, groups);
  return groups.map((group) => query.group(group));
}

export interface GroupingColumnMeta<TRow extends Row> {
  groupLabel?: (row: TRow, emptyValueLabel: string, t: UiTranslate) => string;
}

/** The native grouping column carries the presentation accessor for its axis. */
export function groupedRowLabel<TRow extends Row>(
  row: TableRowModel<TRow>,
  groupStack: readonly ResourceViewGroup[],
  emptyValueLabel: string,
  t: UiTranslate,
): string {
  const columnId = row.groupingColumnId;
  const cell = row.getAllCells().find((candidate) => candidate.column.id === columnId);
  const meta = cell?.column.columnDef.meta as GroupingColumnMeta<TRow> | undefined;
  const original = row.getLeafRows()[0]?.original ?? row.original;
  if (meta?.groupLabel) return meta.groupLabel(original, emptyValueLabel, t);
  const value = columnId ? row.getGroupingValue(columnId) : undefined;
  const group = groupStack[row.depth];
  return group ? groupLabel(value, group, null, emptyValueLabel, t)
    : value == null || value === "" ? emptyValueLabel : String(value);
}

export function bucketValueLabels(
  bucket: AggregateBucket,
  groupStack: readonly ResourceViewGroup[],
  metadata: ModelMetadata | null,
  emptyValueLabel: string,
  t: UiTranslate,
  emptyRelationLabel?: (field: string) => string,
  suppliedQuery?: ResourceQuery,
): string[] {
  const query =
    suppliedQuery ?? (metadata ? ResourceQuery.from(metadata) : null);
  if (!query)
    throw new Error("A resource query is required for grouped buckets.");
  return groupStack.map((group) => {
    const axis = query.group(group);
    const label = axis.bucketLabel(bucket);
    if (axis.declaration.kind === "relation") {
      return label == null || label === ""
        ? emptyRelationLabel?.(group.field) ?? emptyValueLabel
        : String(label);
    }
    return groupLabel(label, group, metadata, emptyValueLabel, t);
  });
}

/** Localized presentation only; identity and bucket extraction belong to GroupAxis. */
export function groupLabel(
  value: unknown,
  group: ResourceViewGroup,
  metadata: ModelMetadata | null,
  emptyValueLabel: string,
  t: UiTranslate,
): string {
  if (value == null || value === "") return emptyValueLabel;
  if (typeof value === "string" && metadata?.fields[group.field]?.kind === "enum") {
    return enumLabelFromMetadata(metadata, group.field, value) ?? value;
  }
  const dateField = metadata?.fields[group.field];
  const isDate = dateField?.scalar === "Date" || dateField?.scalar === "DateTime";
  if (!group.granularity && !isDate) return String(value);
  return groupLabelFromKey(value, group, emptyValueLabel, t);
}

function groupLabelFromKey(
  value: unknown,
  group: ResourceViewGroup,
  emptyValueLabel: string,
  t: UiTranslate,
): string {
  if (value == null || value === "") return emptyValueLabel;
  const key = String(value);
  if (group.granularity === "quarter") {
    const match = /^(\d{4})-Q([1-4])$/.exec(key);
    if (match) {
      return t("list.quarter", {
        year: Number(match[1]),
        quarter: Number(match[2]),
      });
    }
  }
  if (group.granularity === "month") {
    const date = dateFromGroupKey(key);
    if (date) return format(date, "MMMM yyyy");
  }
  if (group.granularity === "week") {
    const date = dateFromGroupKey(key);
    if (date) return t("list.weekOf", { date: format(date, "MMMM d, yyyy") });
  }
  if (!group.granularity) {
    const date = dateFromGroupKey(key);
    if (date) return format(date, "MMMM d, yyyy");
  }
  return key;
}

function dateFromGroupKey(key: string): Date | null {
  const match = /^(\d{4})-(\d{2})(?:-(\d{2}))?$/.exec(key);
  if (!match) return null;
  const date = new Date(
    Number(match[1]),
    Number(match[2]) - 1,
    Number(match[3] ?? "1"),
  );
  return Number.isNaN(date.getTime()) ? null : date;
}

function enumLabelFromMetadata(
  metadata: ModelMetadata | null,
  field: string,
  value: string,
): string | null {
  const fieldMetadata = metadata?.fields[field];
  const values = fieldMetadata?.values ?? [];
  const option = values.find(
    (candidate) =>
      candidate.value === value,
  );
  return option ? enumValueLabel(option) : null;
}
