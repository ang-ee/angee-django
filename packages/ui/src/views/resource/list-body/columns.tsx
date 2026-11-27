import * as React from "react";
import { type CellContext, type Column as TableColumn, type ColumnDef, type HeaderContext, type Row as TableRow, type Table as TableModel } from "@tanstack/react-table";
import { resourceOrderFieldForPath, type FilterPrimitive, type GroupAxis, type QuerySort, type ResourceQuery, type ModelMetadata, type Row } from "@angee/metadata";
import { Glyph } from "../../../chrome/Glyph";
import { DefinitionPair } from "../../../fragments/DefinitionPair";
import { useUiT, type UiTranslate } from "../../../i18n";
import { useResolvedWidget, type WidgetDefinition } from "../../../widgets";
import type { ColumnDescriptor } from "../../page";
import { cellContent, columnLabelText, groupFieldLabel, headerPresses, headerSort, pressedSorting, readPath } from "./cell-utils";
import { groupLabel } from "./grouping";
import { queryForColumns } from "../resource-query";
export interface BuildColumnsOptions {
  /** The list's group axes, as the table state resolves them once (`useResourceViewTableState`). */
  axes?: readonly GroupAxis[];
  /**
   * The direction each of `axes`'s groups go in, by position (`GroupAxis.direction` under the
   * list's order), which its column's comparator carries; ascending where absent.
   */
  directions?: readonly QuerySort["direction"][];
  metadata?: ModelMetadata | null;
  clientOperations?: boolean;
  query?: ResourceQuery;
}

export function buildColumns<TRow extends Row>(
  columns: readonly ColumnDescriptor<TRow>[],
  options: BuildColumnsOptions,
): ColumnDef<TRow>[] {
  const axes = options.axes ?? [];
  const definitions = displayColumns(columns, options);
  if (options.clientOperations) {
    const query = options.query ?? queryForColumns(columns, options.metadata, axes.map((axis) => axis.spec));
    for (const [field, capability] of Object.entries(query.fields)) {
      if (!capability.sort) continue;
      let definition = definitions.find((column) => column.id === field);
      if (!definition) {
        definition = { id: field, enableHiding: false,
          meta: { field, label: groupFieldLabel(field), queryOnly: true } };
        definitions.push(definition);
      }
      const compare = query.comparator(field);
      Object.assign(definition, {
        accessorFn: (row: TRow) => query.value(field, row),
        ...(compare ? { sortingFn: (left: TableRow<TRow>, right: TableRow<TRow>) => compare(left.original, right.original) } : {}),
      });
    }
  }
  for (const [index, axis] of axes.entries()) {
    let definition = definitions.find((column) => column.id === axis.id);
    if (!definition) {
      definition = {
        id: axis.id,
        accessorFn: (row: TRow) => axis.identity(row),
        enableHiding: false,
        meta: { align: "left", label: groupFieldLabel(axis.field), field: axis.field, queryOnly: true },
      };
      definitions.push(definition);
    }
    definition.getGroupingValue = (row: TRow) => axis.identity(row);
    // The table sorts the groups by this column's key, handed ascending (`useResourceViewTableState`),
    // and this comparator carries their direction (`GroupAxis.compareGroups`), since the table
    // would turn a group with no value with a descending key: label, then identity, in that
    // direction, a group with no value last both ways and two of them tied, so its records and an
    // inner level go on to the next key. It runs for every record too (the records of one group tie
    // on it), so it compares each row's key read once per sort (`GroupAxis.groupKey`): its identity
    // is the table's own cached grouping value, and its label, where the axis has one, is kept here
    // by the row the comparator receives. The table copies its rows for every sort, so a kept key
    // lasts one sort, and a record changed in place is read again at the next.
    const direction = options.directions?.[index] ?? "ASC";
    const keys = new WeakMap<TableRow<TRow>, ReturnType<GroupAxis["groupKey"]>>();
    const key = (row: TableRow<TRow>) => keys.get(row)
      ?? keys.set(row, axis.groupKey(row.original, row.getGroupingValue(axis.id) as FilterPrimitive)).get(row)!;
    definition.sortingFn = (left, right) => axis.compareGroups(key(left), key(right), direction);
    // Not the library's: `"first"` and `"last"` decide whenever either value is undefined and never
    // tie two, so the next key is never read, and its default (`1`) decides one undefined value from
    // the column's own value, which a group row reads as undefined when it has none. The comparator
    // decides every comparison from the group's key.
    definition.sortUndefined = false;
    // Here a column's two sorts part ways. Its groups always sort in the table (`enableSorting`, the
    // library's `getCanSort`, which its sorted row model reads, and `listOrderChoice`, which never
    // offers an axis's column), even for a field no read can sort by; its header's press stays the
    // column's own (`displayColumns`), kept as `meta.pressable` (`headerPresses`).
    definition.meta = {
      ...definition.meta,
      pressable: definition.enableSorting !== false,
      groupLabel: (row: TRow, t: Parameters<typeof groupLabel>[3]) =>
        groupLabel(axis.label(row), axis.spec, options.metadata ?? null, t),
    };
    definition.enableSorting = true;
  }
  return definitions;
}

function displayColumns<TRow extends Row>(
  columns: readonly ColumnDescriptor<TRow>[],
  options: BuildColumnsOptions,
): ColumnDef<TRow>[] {
  return columns.map((column) => ({
    id: column.id ?? column.field,
    accessorFn: (row) => readPath(row, column.field),
    enableHiding: column.hideable !== false,
    minSize: column.minWidth,
    enableSorting:
      column.sortable !== false &&
      (options.query
        ? Boolean(options.query.fields[column.field]?.sort)
        : resourceOrderFieldForPath(
            column.field,
            options.metadata?.resource,
          ) !== null),
    sortDescFirst: false,
    header: DisplayHeader,
    cell: DisplayCell,
    meta: {
      align: column.align ?? "left",
      label: column.header ?? column.field,
      field: column.field,
      aggregate: column.aggregate,
      interactive: column.interactive,
      descriptor: { ...column, queryField: column.queryField ?? options.query?.fields[column.field] },
      metadata: options.metadata,
    },
  }));
}

/**
 * Stable component identity keeps a header's sort button mounted, and focused, when the columns
 * are built again (they are when a group's direction turns, `useResourceViewTableState`).
 */
function DisplayHeader<TRow extends Row>({ column: tableColumn, table }: HeaderContext<TRow, unknown>): React.ReactNode {
  // displayColumns owns both this renderer and its typed descriptor metadata.
  const { descriptor: column } = tableColumn.columnDef.meta as { descriptor: ColumnDescriptor<TRow> };
  const label = column.header ?? column.field;
  return (
    <SortHeader column={column} tableColumn={tableColumn} table={table}>
      {column.headerVisuallyHidden ? (
        <span className="sr-only">{label}</span>
      ) : (
        label
      )}
    </SortHeader>
  );
}

/** Stable component identity keeps cell editors mounted when descriptors or drafts change. */
function DisplayCell<TRow extends Row>({ row, column }: CellContext<TRow, unknown>): React.ReactNode {
  // displayColumns owns both this renderer and its typed descriptor metadata.
  const meta = column.columnDef.meta as {
    descriptor: ColumnDescriptor<TRow>;
    metadata?: ModelMetadata | null;
  };
  return <ListCellContent column={meta.descriptor} row={row.original} metadata={meta.metadata} />;
}

export function ListCellContent<TRow extends Row>({
  column,
  row,
  metadata,
}: {
  column: ColumnDescriptor<TRow>;
  row: TRow;
  metadata?: ModelMetadata | null;
}): React.ReactNode {
  const t = useUiT();
  const widget = useResolvedWidget(column.widget ?? "");
  const sublineColumn = column.sublineColumn;
  const sublineWidget = useResolvedWidget(sublineColumn?.widget ?? "");
  if (column.showWhen && !column.showWhen(row)) return null;
  const primary = resolvedCellContent(column, row, t, metadata, widget);
  if (!sublineColumn) return primary;
  return (
    <DefinitionPair
      as="div"
      density="cell"
      detail={resolvedCellContent(
        sublineColumn,
        row,
        t,
        metadata,
        sublineWidget,
      )}
      orientation="stacked"
      value={primary}
    />
  );
}

function resolvedCellContent<TRow extends Row>(
  column: ColumnDescriptor<TRow>,
  row: TRow,
  t: UiTranslate,
  metadata: ModelMetadata | null | undefined,
  widget: WidgetDefinition | undefined,
): React.ReactNode {
  if (!column.render && widget?.cell) {
    const Cell = widget.cell;
    return (
      <Cell
        value={readPath(row, column.field)}
        row={row}
        field={{
          name: column.field,
          label: column.header,
          options: column.options,
          tone: column.tone,
          ...(column.currencyField ? { currencyField: column.currencyField } : {}),
          ...(column.fractionDigitsField ? { fractionDigitsField: column.fractionDigitsField } : {}),
          ...(column.statusDisplay ? { statusDisplay: column.statusDisplay } : {}),
        }}
        readOnly
      />
    );
  }
  return cellContent(column, row, t, metadata);
}

function SortHeader<TRow extends Row>({
  column,
  tableColumn,
  table,
  children,
}: {
  column: ColumnDescriptor<TRow>;
  tableColumn: TableColumn<TRow>;
  table: TableModel<TRow>;
  children: React.ReactNode;
}): React.ReactElement {
  const t = useUiT();
  if (!headerPresses(tableColumn)) return <>{children}</>;
  const sort = headerSort(tableColumn, table);
  const iconName = !sort
    ? "arrow-up-down"
    : sort === "asc"
      ? "arrow-up"
      : "arrow-down";
  const label = columnLabelText(column);
  const sortKey = !sort
    ? "list.sortNotSorted"
    : sort === "asc"
      ? "list.sortAscending"
      : "list.sortDescending";
  return (
    <button
      type="button"
      className="inline-flex min-w-0 items-center gap-1 rounded-6 text-left outline-none hover:text-fg focus-visible:focus-ring"
      aria-label={t(sortKey, { label })}
      // Decided from the list's live order when pressed, since a list re-shows its last settled
      // headers while a read is in flight (`pressedSorting`).
      onClick={() => table.setSorting(pressedSorting(tableColumn, table))}
    >
      <span className="truncate">{children}</span>
      <Glyph name={iconName} className="size-3 text-fg-subtle" />
    </button>
  );
}
