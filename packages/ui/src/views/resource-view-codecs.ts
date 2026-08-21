import { rowPublicId, type DataResourceDefaultSortMetadata, type ModelMetadata, type Row } from "@angee/metadata";
import type {
  GroupingState,
  Row as TableRowModel,
  RowSelectionState,
  SortingState,
} from "@tanstack/react-table";

import type { ColumnDescriptor } from "./page";
import {
  groupedRowLabel,
  type RowGroup,
} from "./resource-view-list-body";
import type {
  ResourceListOrder,
  ResourceViewGroup,
  ResourceViewSort,
} from "./resource-view-model";
import type { UiTranslate } from "../i18n";

interface LaneFieldSource {
  field: string;
  fieldMetadata: { relationObject?: boolean };
}

export function requestedFieldPaths<TRow extends Row>(
  columns: readonly ColumnDescriptor<TRow>[],
  extraFields: readonly string[] | undefined,
  modelMetadata: ModelMetadata | null | undefined,
  laneSource: LaneFieldSource | null | undefined = null,
): readonly string[] {
  // Render-only columns are not real GraphQL fields. When metadata is present,
  // keep only paths whose head belongs to the resource.
  const known = modelMetadata?.resource?.fields;
  const knownNames =
    known && known.length > 0
      ? new Set(known.map((field) => field.name))
      : null;
  const paths = new Set<string>(["id"]);
  for (const column of columns) {
    const head = column.field.split(".", 1)[0] ?? column.field;
    if (knownNames === null || knownNames.has(head)) paths.add(column.field);
  }
  for (const extra of extraFields ?? []) paths.add(extra);
  if (laneSource) {
    paths.add(
      laneSource.fieldMetadata.relationObject === true
        ? `${laneSource.field}.id`
        : laneSource.field,
    );
  }
  return [...paths];
}

export function modelRowId<TRow extends Row>(row: TRow, index: number): string {
  return rowPublicId(row) ?? String(index);
}

export function defaultResourceOrder(
  modelMetadata: ModelMetadata | null | undefined,
): ResourceListOrder | undefined {
  // The resource-hook order input is single-field; project the primary metadata
  // default while preserving its direction.
  const [sort] = modelMetadata?.resource?.defaultSort ?? [];
  if (!sort) return undefined;
  return { [sort.field]: defaultSortDirection(sort) };
}

function defaultSortDirection(
  sort: DataResourceDefaultSortMetadata,
): "ASC" | "DESC" {
  return sort.direction.toUpperCase() === "DESC" ? "DESC" : "ASC";
}

export function stringRowId<TRow extends Row & { id: string }>(row: TRow): string {
  return row.id;
}

export function sortingStateFromResourceSort(
  sort: ResourceViewSort | null,
): SortingState {
  return sort ? [{ id: sort.field, desc: sort.dir === "desc" }] : [];
}

export function groupingStateFromResourceGroups(
  groupStack: readonly ResourceViewGroup[],
): GroupingState {
  return groupStack.map((group) => group.field);
}

export function rowSelectionStateFromIds(
  ids: ReadonlySet<string>,
): RowSelectionState {
  const state: RowSelectionState = {};
  for (const id of ids) state[id] = true;
  return state;
}

export function idsFromRowSelectionState(
  state: RowSelectionState,
): ReadonlySet<string> {
  const ids = new Set<string>();
  for (const [id, selected] of Object.entries(state)) {
    if (selected) ids.add(id);
  }
  return ids;
}

export function leafTableRows<TRow extends Row>(
  rows: readonly TableRowModel<TRow>[],
): readonly TableRowModel<TRow>[] {
  const output: TableRowModel<TRow>[] = [];
  for (const row of rows) {
    if (row.getIsGrouped()) output.push(...leafTableRows(row.subRows));
    else output.push(row);
  }
  return output;
}

export function rowGroupsFromTableRows<TRow extends Row>(
  rows: readonly TableRowModel<TRow>[],
  groupStack: readonly ResourceViewGroup[],
  emptyValueLabel: string,
  t: UiTranslate,
): readonly RowGroup<TRow>[] {
  if (groupStack.length === 0) {
    return [{
      key: "root",
      label: null,
      path: [],
      depth: 0,
      rows: leafTableRows(rows),
      children: [],
    }];
  }
  return rows.map((row) => rowGroupFromTableRow(row, [], groupStack, emptyValueLabel, t));
}

function rowGroupFromTableRow<TRow extends Row>(
  row: TableRowModel<TRow>,
  parentPath: readonly string[],
  groupStack: readonly ResourceViewGroup[],
  emptyValueLabel: string,
  t: UiTranslate,
): RowGroup<TRow> {
  const label = groupedRowLabel(row, groupStack, emptyValueLabel, t);
  const path = [...parentPath, label];
  const children = row.subRows.filter((child) => child.getIsGrouped());
  return {
    key: row.id,
    label,
    path,
    depth: row.depth,
    rows: leafTableRows(row.subRows),
    children: children.map((child) =>
      rowGroupFromTableRow(child, path, groupStack, emptyValueLabel, t),
    ),
  };
}
