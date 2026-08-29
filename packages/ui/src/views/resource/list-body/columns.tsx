import * as React from "react";
import { type ColumnDef } from "@tanstack/react-table";
import type { ModelMetadata, Row } from "@angee/metadata";
import { Glyph } from "../../../chrome/Glyph";
import { useUiT } from "../../../i18n";
import { useResolvedWidget } from "../../../widgets";
import type { ResourceViewContextValue } from "../resource-view-context";
import type { ResourceViewGroup, ResourceViewSort } from "../resource-view-model";
import type { ColumnDescriptor } from "../../page";
import { cellContent, columnLabelText, groupFieldLabel, nextSort, readPath } from "./cell-utils";
import { groupKey } from "./grouping";
export interface BuildColumnsOptions {
  groupStack?: readonly ResourceViewGroup[];
  metadata?: ModelMetadata | null;
}

export function buildColumns<TRow extends Row>(
  columns: readonly ColumnDescriptor<TRow>[],
  sortController: Pick<ResourceViewContextValue, "setSort"> & {
    sort: ResourceViewSort | null;
  },
  options: BuildColumnsOptions,
): ColumnDef<TRow>[] {
  // TanStack grouping requires a column def per grouping id; a group axis that
  // is not a display column gets a grouping-only accessor column (never
  // rendered, hidden from the column chooser via `meta.groupingOnly`).
  const groupOnlyColumns: ColumnDef<TRow>[] = (options.groupStack ?? [])
    .filter((group) => !columns.some((column) => column.field === group.field))
    .map((group) => ({
      id: group.field,
      accessorFn: (row: TRow) => readPath(row, group.field),
      getGroupingValue: (row: TRow) =>
        groupKey(
          readPath(row, group.field),
          group,
          options.metadata ?? null,
        ),
      enableHiding: false,
      meta: {
        align: "left",
        label: groupFieldLabel(group.field),
        field: group.field,
        groupingOnly: true,
      },
    }));
  return [...displayColumns(columns, sortController, options), ...groupOnlyColumns];
}

function displayColumns<TRow extends Row>(
  columns: readonly ColumnDescriptor<TRow>[],
  sortController: Pick<ResourceViewContextValue, "setSort"> & {
    sort: ResourceViewSort | null;
  },
  options: BuildColumnsOptions,
): ColumnDef<TRow>[] {
  return columns.map((column) => ({
    id: column.field,
    accessorFn: (row) => readPath(row, column.field),
    getGroupingValue: (row) => {
      const group = options.groupStack?.find((item) => item.field === column.field);
      if (!group) return readPath(row, column.field);
      return groupKey(
        readPath(row, column.field),
        group,
        options.metadata ?? null,
      );
    },
    header: () => {
      const label = column.header ?? column.field;
      return (
        <SortHeader column={column} sortController={sortController}>
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
  sortController,
  children,
}: {
  column: ColumnDescriptor<TRow>;
  sortController: Pick<ResourceViewContextValue, "setSort"> & {
    sort: ResourceViewSort | null;
  };
  children: React.ReactNode;
}): React.ReactElement {
  const t = useUiT();
  if (column.sortable === false) return <>{children}</>;
  const sort = sortController.sort;
  const active = sort?.field === column.field;
  const iconName = !active
    ? "arrow-up-down"
    : sort.dir === "asc"
      ? "arrow-up"
      : "arrow-down";
  const label = columnLabelText(column);
  const sortKey = !active
    ? "list.sortNotSorted"
    : sort.dir === "asc"
      ? "list.sortAscending"
      : "list.sortDescending";
  return (
    <button
      type="button"
      className="inline-flex min-w-0 items-center gap-1 rounded-6 text-left outline-none hover:text-fg focus-visible:focus-ring"
      aria-label={t(sortKey, { label })}
      onClick={() => sortController.setSort(nextSort(sort, column.field))}
    >
      <span className="truncate">{children}</span>
      <Glyph name={iconName} className="size-3 text-fg-subtle" />
    </button>
  );
}
