import { isClientRowModel, rowPublicId, type DataResourceMetadata, type ResourceQuery, type ModelMetadata, type Row } from "@angee/metadata";
import type {
  GroupingState,
  Row as TableRowModel,
  RowSelectionState,
  SortingState,
} from "@tanstack/react-table";

import type { ColumnDescriptor } from "../page";
import {
  groupedRowLabel,
  tableGroupAxes,
  type RowGroup,
} from "./resource-view-list-body";
import type {
  ResourceListOrder,
  ResourceViewGroup,
} from "./resource-view-model";
import type { UiTranslate } from "../../i18n";
import { queryForColumns } from "./resource-query";

interface LaneFieldSource {
  field: string;
  fieldMetadata: { relationObject?: boolean | null };
  rankField?: string;
}

export function requestedFieldPaths<TRow extends Row>(
  columns: readonly ColumnDescriptor<TRow>[],
  extraFields: readonly string[] | undefined,
  modelMetadata: ModelMetadata | null | undefined,
  laneSource: LaneFieldSource | null | undefined = null,
  groups: readonly ResourceViewGroup[] = [],
): readonly string[] {
  // Render-only columns are not real GraphQL fields. When metadata is present,
  // keep only paths whose head belongs to the resource.
  const known = modelMetadata?.resource?.fields;
  const knownNames =
    known && known.length > 0
      ? new Set(known.map((field) => field.name))
      : null;
  const query = queryForColumns(columns, modelMetadata, groups);
  const paths = new Set<string>([query.contract.identity.field]);
  for (const column of columns) {
    const selections = column.selectionPaths ?? query.fields[column.field]?.row?.paths ?? [column.field];
    for (const path of selections) {
      const head = path.split(".", 1)[0] ?? path;
      if (knownNames === null || knownNames.has(head)) paths.add(path);
    }
  }
  for (const extra of extraFields ?? []) paths.add(extra);
  const requiredGroups = laneSource && !groups.some((group) => group.field === laneSource.field)
    ? [...groups, { field: laneSource.field }] : groups;
  for (const axis of tableGroupAxes(requiredGroups, modelMetadata, columns)) {
    // Server summary axes may have no row projection. Their declared paths are
    // sufficient here; client grouping validates its identity when it is used.
    for (const path of axis.declaration.paths) paths.add(path);
  }
  if (laneSource?.rankField) paths.add(laneSource.rankField);
  if (modelMetadata && isClientRowModel(modelMetadata.resource)) {
    for (const path of query.clientSelection()) paths.add(path);
  }
  return [...paths];
}

export function modelRowId<TRow extends Row>(row: TRow, index: number, resource?: Pick<DataResourceMetadata, "query"> | null): string {
  return rowPublicId(row, resource) ?? String(index);
}

export function defaultResourceOrder(
  modelMetadata: ModelMetadata | null | undefined,
): ResourceListOrder | undefined {
  const sorts = modelMetadata?.resource.query.sort.default;
  if (!sorts?.length) return undefined;
  return Object.fromEntries(sorts.map((sort) => [sort.field, sort.direction]));
}

/** Seed the native initial sorting at a declaring list's provider boundary. */
export function initialResourceSorting(
  modelMetadata: ModelMetadata | null | undefined,
  order?: ResourceListOrder,
): SortingState | undefined {
  const declared = order ?? defaultResourceOrder(modelMetadata);
  if (declared === undefined) return undefined;
  const query = queryForColumns(Object.keys(declared).map((field) => ({ field })), modelMetadata);
  return query.sortFrom(declared).map(({ field, direction }) => ({ id: field, desc: direction === "DESC" }));
}

export function groupingStateFromResourceGroups<TRow extends object>(
  groupStack: readonly ResourceViewGroup[],
  metadata?: ModelMetadata | null,
  columns: readonly ColumnDescriptor<TRow>[] = [],
  query?: ResourceQuery,
): GroupingState {
  return tableGroupAxes(groupStack, metadata, columns, query).map((axis) => axis.id);
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
