import * as React from "react";
import { flexRender, type Cell as TableCellModel, type Column as TableColumn, type ColumnDef, type RowData, type SortDirection, type SortingState, type Table as TableModel } from "@tanstack/react-table";
import type { AggregateBucket, AggregateMeasureOperator } from "@angee/refine";
import type {
  GroupAxis,
  ModelMetadata,
  Row,
} from "@angee/metadata";
import { isDateField, rowValueAtPath, resourceFieldPathToSnake } from "@angee/metadata";
import { type UiTranslate } from "../../../i18n";
import { enumValueLabel, groupFieldLabel, statusLabel } from "../../../lib/labels";
import { formatNumber, isNumericString } from "../../../lib/format-number";
import { titleCase } from "../../../lib/titleCase";
import { Chip } from "../../../ui/chip";
import { formatJson } from "../../../widgets/json-format";
import { ChipList } from "../../../ui/chip";
import { dateFromUnknown, formatTime } from "../../../widgets/date-format";
import { canonicalOptionValue } from "../../../widgets/types";
import { RecordReferenceChips } from "../../relation/RecordReference";
import { AbsoluteTime } from "../../../fragments/AbsoluteTime";
import { columnTone } from "../../page";
import type { ColumnAggregate, ColumnDescriptor, PageColumnAlign } from "../../page";
import { querySortFromSorting } from "../model/state";
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
    return <Chip tone={tone}>{enumValue?.label ?? (label ? statusLabel(label) : "—")}</Chip>;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return "—";
    return <ChipList items={value.map((item, index) => ({ id: `${String(item)}:${index}`, label: String(item) }))} />;
  }
  if (enumValue) return enumValue.label;
  const date = isDateField(field, column.field)
    ? dateFromUnknown(value)
    : null;
  if (date) return <AbsoluteTime display="date" value={typeof value === "string" ? value : date} />;
  if (field?.kind === "scalar" && field.scalar === "Time" && typeof value === "string") {
    const time = formatTime(value);
    if (time) return <time dateTime={value} className="tabular-nums">{time}</time>;
  }
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

declare module "@tanstack/react-table" {
  interface TableMeta<TData extends RowData> {
    /**
     * The list's group axes (`useResourceViewTableState`): which sort keys a group's records share.
     * A table of your own that renders `ListHeaderCell` sets it for the same answer.
     */
    groupAxes?: readonly GroupAxis[];
    /**
     * The list's order as its view holds it (`useResourceViewTableState`): what the list stores,
     * puts in its URL and saves in a view. The table's own sorting leads it with each group axis's
     * key, ascending, whose column's comparator carries the groups' direction (`buildColumns`), so
     * the headers, a press and the Order select read this one. Absent, they read the table's sorting.
     */
    sorting?: SortingState;
  }
}

/** The list's order (`TableMeta.sorting`), else the table's sorting. */
function listSorting<TRow extends Row>(table: TableModel<TRow>): SortingState {
  return table.options.meta?.sorting ?? table.getState().sorting;
}

/** The first group axis of the list whose records share the key `id` (`GroupAxis.shares`). */
function sharingAxis<TRow extends Row>(table: TableModel<TRow>, id: string): GroupAxis | undefined {
  return (table.options.meta?.groupAxes ?? []).find((axis) => axis.shares(id));
}

/** Whether `axis`'s groups go descending under the list's order (`GroupAxis.direction`). */
function groupsDescending<TRow extends Row>(axis: GroupAxis, table: TableModel<TRow>): boolean {
  return axis.direction(querySortFromSorting(listSorting(table))) === "DESC";
}

/** The keys the rows visibly follow, in order: the list's order without the keys a group axis shares. */
function followedSorting<TRow extends Row>(table: TableModel<TRow>): SortingState {
  return listSorting(table).filter(({ id }) => !sharingAxis(table, id));
}

/**
 * The sort a column's header shows, read from the list's order (`TableMeta.sorting`): its sort
 * button's arrow and accessible name read it. A column whose key a group axis of the list shares
 * (`GroupAxis.shares`: the field grouped by its whole value, a `Date` grouped by day or a finer
 * unit included, and a grouped relation's members) shows the direction that axis's groups go
 * in (`GroupAxis.direction`), always: groups are never unsorted. Any other column shows the
 * direction of the first key the rows follow, the first of the order no axis shares; every
 * other header shows none, a later key's included (it only breaks ties, `breaksTies`). A header
 * that does not press (`headerPresses`) has no sort button, so shows none of it. A press
 * sets the order `pressedSorting` gives. The URL (`model/search.ts`) and a saved view
 * (`model/favorites.ts`) keep the whole order.
 */
export function headerSort<TRow extends Row>(
  column: TableColumn<TRow, unknown>,
  table: TableModel<TRow>,
): false | SortDirection {
  const axis = sharingAxis(table, column.id);
  if (axis) return groupsDescending(axis, table) ? "desc" : "asc";
  const [followed] = followedSorting(table);
  return followed?.id === column.id ? (followed.desc ? "desc" : "asc") : false;
}

/**
 * What a column header's `aria-sort` says, which WAI-ARIA puts on one header at a time: the
 * direction of the first key the rows follow, on that key's own header (`headerSort`), and
 * `"none"` on every other. Here what a header shows and what it says part ways, for a column
 * whose key a group axis shares, and only there: it shows its groups' direction, and says
 * `"none"`, since the records follow another key, or none.
 */
export function headerAriaSort<TRow extends Row>(
  column: TableColumn<TRow, unknown>,
  table: TableModel<TRow>,
): "ascending" | "descending" | "none" {
  const sort = !sharingAxis(table, column.id) && headerSort(column, table);
  return sort === "asc" ? "ascending" : sort === "desc" ? "descending" : "none";
}

/**
 * Whether a column's header presses (`pressedSorting`): a group axis's column keeps its own
 * press (`ListColumnMeta.pressable`), since it always sorts in the table; any other presses when
 * the table can sort by it.
 */
export function headerPresses<TRow extends Row>(column: TableColumn<TRow, unknown>): boolean {
  return columnMeta(column.columnDef).pressable ?? column.getCanSort();
}

/**
 * The order a press on `column`'s header sets, decided from the list's live order
 * (`TableMeta.sorting`): one rule for every press. A press on a column whose key a group axis
 * shares (`headerSort`) turns that axis's groups: it sets the key to the direction its header
 * does not show, so it turns between ascending and descending and is never removed, and the
 * other keys that axis shares go. On any other column a press is the table's own single-sort
 * toggle (`enableMultiSort: false`) over the keys the rows follow: on the last of them it turns
 * that key to its next direction (`getNextSortingOrder`) or removes it; on any other column it
 * sets that column alone in its next direction, except that a column that only breaks ties sorts
 * in its first direction (`getFirstSortDir`), where the toggle would turn it. The pressed key
 * leads the order it sets, a grouping column's wherever the order held it (the table state hands
 * an axis's key ahead of the others whatever its place), and what a press keeps stays behind it
 * in the order the list's order holds it: a press on a grouping column keeps every key its axis
 * does not share, the records' keys and another level's, so turning the groups leaves the
 * records' order alone; a press on another column keeps the keys a group axis shares, so it
 * never drops them and never turns the groups. Given `desc`, it is the order choosing a column
 * no group shares in that direction sets (the pane toolbar's Order select, `listOrderChoice`).
 */
export function pressedSorting<TRow extends Row>(
  column: TableColumn<TRow, unknown>,
  table: TableModel<TRow>,
  desc?: boolean,
): SortingState {
  const axis = sharingAxis(table, column.id);
  const kept = listSorting(table).filter(({ id }) => (axis ? !axis.shares(id) : sharingAxis(table, id)));
  if (axis) return [{ id: column.id, desc: !groupsDescending(axis, table) }, ...kept];
  if (desc !== undefined) return [{ id: column.id, desc }, ...kept];
  if (breaksTies(column, table)) return [{ id: column.id, desc: column.getFirstSortDir() === "desc" }, ...kept];
  const next = column.getNextSortingOrder();
  // The only key the rows follow, since a later one only breaks ties.
  if (followedSorting(table).at(-1)?.id === column.id && !next) return kept;
  return [{ id: column.id, desc: next === "desc" }, ...kept];
}

/** A list's order as its pane toolbar's Order select offers it (`listOrderChoice`), by column field. */
export interface ListOrderChoice {
  /** The first key the rows follow (`headerSort` shows it on its column, if there is one). */
  shown?: { field: string; desc: boolean };
  /**
   * Whether the select offers `field`: a column the table can sort by (`getCanSort`, which for a
   * column no group axis shares is its header's press) whose key no group of the list shares,
   * since such a key orders the groups, not the rows the select orders; its own header turns them.
   */
  offers: (field: string) => boolean;
  /** Sort by `field` in that direction; nothing when it is what is shown or is not offered. */
  choose: (field: string, desc: boolean) => void;
}

/**
 * The Order select over a list's table: it shows what the headers show, and chooses through
 * `pressedSorting` with the chosen direction, on the column the header itself presses.
 */
export function listOrderChoice<TRow extends Row>(table: TableModel<TRow>): ListOrderChoice {
  const columns = table.getAllLeafColumns();
  const fieldOf = (column: TableColumn<TRow, unknown>) => columnMeta(column.columnDef).field ?? column.id;
  const columnOf = (field: string) => columns.find((column) => column.id === field) ?? columns.find((column) => fieldOf(column) === field);
  const offered = (column: TableColumn<TRow, unknown> | undefined): column is TableColumn<TRow, unknown> =>
    Boolean(column?.getCanSort() && !sharingAxis(table, column.id));
  const [followed] = followedSorting(table);
  const followedColumn = followed && columns.find((column) => column.id === followed.id);
  const shown = followed && { field: followedColumn ? fieldOf(followedColumn) : followed.id, desc: followed.desc };
  return {
    ...(shown ? { shown } : {}),
    offers: (field) => offered(columnOf(field)),
    choose: (field, desc) => {
      const column = columnOf(field);
      if (!offered(column) || (shown?.field === field && shown.desc === desc)) return;
      table.setSorting(pressedSorting(column, table, desc));
    },
  };
}

/** Whether a column only breaks ties: a later key of the order the rows follow (`headerSort`). */
function breaksTies<TRow extends Row>(
  column: TableColumn<TRow, unknown>,
  table: TableModel<TRow>,
): boolean {
  return followedSorting(table).slice(1).some(({ id }) => id === column.id);
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
    ? ""
    : typeof value === "number"
    || typeof value === "bigint"
    || (typeof value === "string" && isNumericString(value))
      ? formatNumber(value)
      : String(value);
  return measure.unit ? `${formatted} ${measure.unit}` : formatted;
}

function displayValue(value: unknown, t: UiTranslate): React.ReactNode {
  if (value == null || value === "") return "—";
  if (typeof value === "boolean") return t(value ? "list.yes" : "list.no");
  if (typeof value === "object") return formatJson(value, { compact: true });
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

/** A list column's `meta` as the list reads it, which `displayColumns` and `buildColumns` write. */
interface ListColumnMeta {
  align?: PageColumnAlign;
  label?: React.ReactNode;
  field?: string;
  aggregate?: ColumnAggregate;
  queryOnly?: boolean;
  interactive?: boolean;
  /**
   * Whether a group axis's column presses (`headerPresses`), which `buildColumns` keeps from the
   * column's own sort (`displayColumns`) before it lets the table sort the axis's groups by it.
   */
  pressable?: boolean;
}

function columnMeta<TRow extends Row>(column: ColumnDef<TRow>): ListColumnMeta {
  return (column.meta as ListColumnMeta | undefined) ?? {};
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
