import * as React from "react";
import { type CellContext, type Column as TableColumn, type ColumnDef, type Row as TableRow, type Table as TableModel } from "@tanstack/react-table";
import { resourceOrderFieldForPath, type ResourceQuery, type ModelMetadata, type Row } from "@angee/metadata";
import { Glyph } from "../../../chrome/Glyph";
import { DefinitionPair } from "../../../fragments/DefinitionPair";
import { useUiT, type UiTranslate } from "../../../i18n";
import { useResolvedWidget, type WidgetDefinition } from "../../../widgets";
import { StatusBadge } from "../../../widgets/statusBadge";
import type { ResourceViewGroup } from "../resource-view-model";
import type { ColumnDescriptor } from "../../page";
import { breaksTies, cellContent, columnLabelText, groupFieldLabel, headerSort, readPath } from "./cell-utils";
import { groupLabel, tableGroupAxes } from "./grouping";
import { queryForColumns } from "../resource-query";
import { isUnregisteredImplementation } from "../implementation-fields";
export interface BuildColumnsOptions {
  groupStack?: readonly ResourceViewGroup[];
  metadata?: ModelMetadata | null;
  clientOperations?: boolean;
  query?: ResourceQuery;
}

export function buildColumns<TRow extends Row>(
  columns: readonly ColumnDescriptor<TRow>[],
  options: BuildColumnsOptions,
): ColumnDef<TRow>[] {
  const axes = tableGroupAxes(options.groupStack ?? [], options.metadata, columns, options.query);
  const definitions = displayColumns(columns, options);
  if (options.clientOperations) {
    const query = options.query ?? queryForColumns(columns, options.metadata, options.groupStack);
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
  for (const axis of axes) {
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
    definition.meta = {
      ...definition.meta,
      groupLabel: (row: TRow, t: Parameters<typeof groupLabel>[3]) =>
        groupLabel(axis.label(row), axis.spec, options.metadata ?? null, t),
    };
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
    header: ({ column: tableColumn, table }) => {
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
    },
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

const NOT_REGISTERED = "not_registered";
const NOT_REGISTERED_TONE = { [NOT_REGISTERED]: "warning" } as const;

function resolvedCellContent<TRow extends Row>(
  column: ColumnDescriptor<TRow>,
  row: TRow,
  t: UiTranslate,
  metadata: ModelMetadata | null | undefined,
  widget: WidgetDefinition | undefined,
): React.ReactNode {
  if (!column.render && isUnregisteredImplementation(metadata, column.field, readPath(row, column.field))) {
    // The record form marks the same fact with the warning tone; the cell wears it through the badge.
    return <StatusBadge value={NOT_REGISTERED} tone={NOT_REGISTERED_TONE} display={column.statusDisplay}>
      {t("implementation.notRegistered")}
    </StatusBadge>;
  }
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
  if (!tableColumn.getCanSort()) return <>{children}</>;
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
      // Decided from the table's live state when pressed, since a list re-shows its last settled
      // headers while a read is in flight: a tie-breaker sorts in its first direction, every
      // other press is the table's toggle (`headerSort`).
      onClick={(event) => breaksTies(tableColumn, table)
        ? table.setSorting([{ id: tableColumn.id, desc: tableColumn.getFirstSortDir() === "desc" }])
        : tableColumn.getToggleSortingHandler()?.(event)}
    >
      <span className="truncate">{children}</span>
      <Glyph name={iconName} className="size-3 text-fg-subtle" />
    </button>
  );
}
