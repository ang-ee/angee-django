import * as React from "react";
import { type Column as TableColumn, type ColumnDef, type Row as TableRow } from "@tanstack/react-table";
import { resourceOrderFieldForPath, type ResourceQuery, type ModelMetadata, type Row } from "@angee/metadata";
import { Glyph } from "../../../chrome/Glyph";
import { useUiT } from "../../../i18n";
import { useResolvedWidget } from "../../../widgets";
import type { ResourceViewGroup } from "../resource-view-model";
import type { ColumnDescriptor } from "../../page";
import { cellContent, columnLabelText, groupFieldLabel, readPath } from "./cell-utils";
import { groupLabel, tableGroupAxes } from "./grouping";
import { queryForColumns } from "../resource-query";
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
      groupLabel: (row: TRow, emptyValueLabel: string, t: Parameters<typeof groupLabel>[4]) =>
        groupLabel(axis.label(row), axis.spec, options.metadata ?? null, emptyValueLabel, t),
    };
  }
  return definitions;
}

function displayColumns<TRow extends Row>(
  columns: readonly ColumnDescriptor<TRow>[],
  options: BuildColumnsOptions,
): ColumnDef<TRow>[] {
  return columns.map((column) => ({
    id: column.field,
    accessorFn: (row) => readPath(row, column.field),
    enableSorting:
      column.sortable !== false &&
      (options.query
        ? Boolean(options.query.fields[column.field]?.sort)
        : resourceOrderFieldForPath(
            column.field,
            options.metadata?.resource,
          ) !== null),
    sortDescFirst: false,
    header: ({ column: tableColumn }) => {
      const label = column.header ?? column.field;
      return (
        <SortHeader column={column} tableColumn={tableColumn}>
          {column.headerVisuallyHidden ? (
            <span className="sr-only">{label}</span>
          ) : (
            label
          )}
        </SortHeader>
      );
    },
    cell: ({ row }) => (
      <ListCellContent
        column={column}
        row={row.original}
        metadata={options.metadata}
      />
    ),
    meta: {
      align: column.align ?? "left",
      label: column.header ?? column.field,
      field: column.field,
      aggregate: column.aggregate,
      interactive: column.interactive,
    },
  }));
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
  children,
}: {
  column: ColumnDescriptor<TRow>;
  tableColumn: TableColumn<TRow>;
  children: React.ReactNode;
}): React.ReactElement {
  const t = useUiT();
  if (!tableColumn.getCanSort()) return <>{children}</>;
  const sort = tableColumn.getIsSorted();
  const active = Boolean(sort);
  const iconName = !active
    ? "arrow-up-down"
    : sort === "asc"
      ? "arrow-up"
      : "arrow-down";
  const label = columnLabelText(column);
  const sortKey = !active
    ? "list.sortNotSorted"
    : sort === "asc"
      ? "list.sortAscending"
      : "list.sortDescending";
  return (
    <button
      type="button"
      className="inline-flex min-w-0 items-center gap-1 rounded-6 text-left outline-none hover:text-fg focus-visible:focus-ring"
      aria-label={t(sortKey, { label })}
      onClick={tableColumn.getToggleSortingHandler()}
    >
      <span className="truncate">{children}</span>
      <Glyph name={iconName} className="size-3 text-fg-subtle" />
    </button>
  );
}
