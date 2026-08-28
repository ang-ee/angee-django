import * as React from "react";
import {
  fieldUpdatable,
  refineResourceName,
  rowPublicId,
  type ModelFieldMetadata,
  type ModelMetadata,
  type Row,
} from "@angee/metadata";
import { useUpdate, type BaseRecord, type HttpError } from "@refinedev/core";
import type { Row as TableRowModel } from "@tanstack/react-table";

import { errorMessage, useToast } from "../../feedback";
import { useUiT } from "../../i18n";
import { useLatestRef } from "../../lib/use-latest-ref";
import { relationValueId } from "../../widgets/types";
import type { RelationOption } from "../../widgets/RelationField";
import type { RelationFieldInfo } from "./model-metadata-defaults";
import {
  relationSelectedOption,
  useRelationOptions,
} from "../relation/relation-options";
import { leafTableRows } from "./resource-view-codecs";
import { readPath, type RowGroup } from "./resource-view-list-body";
import type {
  BoardCardPlacement,
  BoardLaneSource,
} from "./resource-view-types";

type RowRecord = BaseRecord & Row;

export interface ResolvedBoardLaneSource extends BoardLaneSource {
  relation: RelationFieldInfo;
  fieldMetadata: NonNullable<ModelMetadata["fields"][string]>;
  rankFieldMetadata?: ModelFieldMetadata;
  foldFieldMetadata?: ModelFieldMetadata;
}

export interface BoardLaneOption extends RelationOption {
  defaultCollapsed?: boolean;
}

export interface BoardLaneState<TRow extends Row> {
  source: ResolvedBoardLaneSource | null;
  lanes: readonly BoardLaneOption[];
  optimisticPlacementByRowId: ReadonlyMap<string, BoardCardPlacement>;
  fetching: boolean;
  dragEnabled: boolean;
  rankField?: string;
  onCardMove?: (
    row: TRow,
    laneId: string | null,
    rank?: number,
  ) => Promise<void>;
}

interface OptimisticLaneEntry {
  laneId: string;
  rank?: number;
  serverLaneAtMove: string;
  serverRankAtMove?: number;
  settled: boolean;
}

const EMPTY_OPTIMISTIC_LANE_ENTRIES: ReadonlyMap<string, OptimisticLaneEntry> =
  new Map();

export function useBoardLaneState<TRow extends Row>({
  laneSource,
  modelMetadata,
  rows,
  enabled,
  refetchRows,
}: {
  laneSource: ResolvedBoardLaneSource | null | undefined;
  modelMetadata: ModelMetadata | null | undefined;
  rows: readonly TRow[];
  enabled: boolean;
  refetchRows?: () => void;
}): BoardLaneState<TRow> {
  const t = useUiT();
  const toast = useToast();
  const source = enabled ? (laneSource ?? null) : null;
  const laneFields = React.useMemo(
    () => source?.foldField ? [source.foldField] : [],
    [source?.foldField],
  );
  const laneResult = useRelationOptions(source?.relation ?? null, {
    labelField: source?.labelField,
    fields: laneFields,
    filters: source?.filters,
    sorters: source?.sorters,
    enabled: source !== null,
  });
  // Refine's optimistic cache writes a flat relation id, which conflicts with
  // rows that read nested relation objects; this map preserves the display lane.
  const [optimisticLaneEntryByRowId, setOptimisticLaneEntryByRowId] =
    React.useState<ReadonlyMap<string, OptimisticLaneEntry>>(
      EMPTY_OPTIMISTIC_LANE_ENTRIES,
    );
  const optimisticPlacementByRowId = React.useMemo(
    () => {
      const placements = new Map<string, BoardCardPlacement>();
      for (const [id, entry] of optimisticLaneEntryByRowId) {
        placements.set(id, {
          laneId: entry.laneId,
          ...(entry.rank === undefined ? {} : { rank: entry.rank }),
        });
      }
      return placements;
    },
    [optimisticLaneEntryByRowId],
  );
  const lanes = React.useMemo<readonly BoardLaneOption[]>(() => {
    if (!source?.foldField) return laneResult.options;
    const foldField = source.foldField;
    const rowById = new Map(
      laneResult.rows.flatMap((row) => {
        const id = rowPublicId(row);
        return id ? [[id, row] as const] : [];
      }),
    );
    return laneResult.options.map((option) => ({
      ...option,
      ...(readPath(rowById.get(option.value) ?? {}, foldField) === true
        ? { defaultCollapsed: true }
        : {}),
    }));
  }, [laneResult.options, laneResult.rows, source?.foldField]);
  const dataResource = modelMetadata?.resource ?? null;
  const update = useUpdate<RowRecord, HttpError, Record<string, unknown>>({
    resource: source && dataResource
      ? refineResourceName(dataResource)
      : "__angee_disabled__",
    dataProviderName: source ? dataResource?.schemaName : undefined,
    invalidates: ["list", "many", "detail"],
    successNotification: false,
    errorNotification: false,
  });
  const updateRef = useLatestRef(update);
  const dragEnabled = boardLaneDragEnabled(source, modelMetadata);

  React.useEffect(() => {
    if (!source || optimisticLaneEntryByRowId.size === 0) return;
    setOptimisticLaneEntryByRowId((current) => {
      let changed = false;
      const next = new Map(current);
      for (const row of rows) {
        const id = rowPublicId(row);
        if (!id) continue;
        const optimisticLane = current.get(id);
        const serverLane = relationValueId(readPath(row, source.field));
        const serverRank = source.rankField
          ? finiteRank(readPath(row, source.rankField))
          : undefined;
        if (
          optimisticLane !== undefined
          && optimisticLane.settled
          && (
            (
              serverLane === optimisticLane.laneId
              && ranksEqual(serverRank, optimisticLane.rank)
            )
            || serverLane !== optimisticLane.serverLaneAtMove
            || !ranksEqual(serverRank, optimisticLane.serverRankAtMove)
          )
        ) {
          next.delete(id);
          changed = true;
        }
      }
      return changed ? next : current;
    });
  }, [optimisticLaneEntryByRowId, rows, source]);

  const onCardMove = React.useCallback(
    async (
      row: TRow,
      laneId: string | null,
      rank?: number,
    ): Promise<void> => {
      if (!dragEnabled || !source || !dataResource) return;
      if (laneId === null && !laneSourceNullable(source)) return;
      if (source.rankField && rank === undefined) return;
      const id = rowPublicId(row);
      if (!id) return;
      const serverLane = relationValueId(readPath(row, source.field));
      const serverRank = source.rankField
        ? finiteRank(readPath(row, source.rankField))
        : undefined;
      const currentPlacement = optimisticLaneEntryByRowId.get(id);
      const currentLane = currentPlacement?.laneId ?? serverLane;
      const currentRank = currentPlacement?.rank ?? serverRank;
      const nextLane = laneId ?? "";
      if (
        currentLane === nextLane
        && (!source.rankField || ranksEqual(currentRank, rank))
      ) return;
      setOptimisticLaneEntryByRowId((current) => {
        const next = new Map(current);
        next.set(id, {
          laneId: nextLane,
          ...(rank === undefined ? {} : { rank }),
          serverLaneAtMove: serverLane,
          ...(serverRank === undefined ? {} : { serverRankAtMove: serverRank }),
          settled: false,
        });
        return next;
      });
      try {
        await updateRef.current.mutateAsync({
          id,
          values: {
            [source.field]: laneId,
            ...(source.rankField && rank !== undefined
              ? { [source.rankField]: rank }
              : {}),
          },
        });
        setOptimisticLaneEntryByRowId((current) => {
          const entry = current.get(id);
          if (
            !entry
            || entry.laneId !== nextLane
            || (source.rankField && !ranksEqual(entry.rank, rank))
          ) return current;
          const next = new Map(current);
          next.set(id, { ...entry, settled: true });
          return next;
        });
      } catch (error) {
        setOptimisticLaneEntryByRowId((current) => {
          const entry = current.get(id);
          if (
            !entry
            || entry.laneId !== nextLane
            || (source.rankField && !ranksEqual(entry.rank, rank))
          ) return current;
          const next = new Map(current);
          next.delete(id);
          return next;
        });
        toast.danger({
          title: t("board.moveFailed"),
          description: errorMessage(error, t("board.moveFailedDescription")),
        });
        refetchRows?.();
      }
    },
    [
      dataResource,
      dragEnabled,
      optimisticLaneEntryByRowId,
      refetchRows,
      source,
      t,
      toast,
      updateRef,
    ],
  );

  return React.useMemo(
    () => ({
      source,
      lanes,
      optimisticPlacementByRowId,
      fetching: laneResult.list.fetching,
      dragEnabled,
      ...(source?.rankField ? { rankField: source.rankField } : {}),
      ...(dragEnabled ? { onCardMove } : {}),
    }),
    [
      dragEnabled,
      laneResult.list.fetching,
      lanes,
      onCardMove,
      optimisticPlacementByRowId,
      source,
    ],
  );
}

export function rowGroupsFromLaneSource<TRow extends Row>(
  rows: readonly TableRowModel<TRow>[],
  laneSource: ResolvedBoardLaneSource,
  lanes: readonly BoardLaneOption[],
  optimisticPlacementByRowId: ReadonlyMap<string, BoardCardPlacement>,
  emptyValueLabel: string,
  unknownValueLabel: string,
): readonly RowGroup<TRow>[] {
  const leafRows = leafTableRows(rows);
  const rowsByLane = new Map<string, TableRowModel<TRow>[]>();
  const declaredLaneIds = new Set(lanes.map((lane) => lane.value));
  for (const row of leafRows) {
    const rowId = rowPublicId(row.original) ?? row.id;
    const laneId =
      optimisticPlacementByRowId.get(rowId)?.laneId
      ?? relationValueId(readPath(row.original, laneSource.field));
    const laneRows = rowsByLane.get(laneId);
    if (laneRows) laneRows.push(row);
    else rowsByLane.set(laneId, [row]);
  }

  const groups = lanes.map((lane) =>
    laneGroup(
      lane.value,
      lane.label,
      orderedLaneRows(
        rowsByLane.get(lane.value) ?? [],
        laneSource.rankField,
        optimisticPlacementByRowId,
      ),
      { defaultCollapsed: lane.defaultCollapsed },
    ),
  );
  for (const [laneId, laneRows] of rowsByLane) {
    if (!declaredLaneIds.has(laneId)) {
      groups.push(laneGroup(
        laneId,
        undeclaredLaneLabel(
          laneRows,
          laneSource,
          laneId,
          emptyValueLabel,
          unknownValueLabel,
        ),
        orderedLaneRows(
          laneRows,
          laneSource.rankField,
          optimisticPlacementByRowId,
        ),
        { dropDisabled: laneId === "" && !laneSourceNullable(laneSource) },
      ));
    }
  }
  return groups;
}

function laneGroup<TRow extends Row>(
  id: string,
  label: string,
  rows: readonly TableRowModel<TRow>[],
  options: { dropDisabled?: boolean; defaultCollapsed?: boolean } = {},
): RowGroup<TRow> {
  return {
    key: id,
    label,
    path: [label],
    depth: 0,
    rows,
    children: [],
    declared: true,
    ...(options.dropDisabled ? { dropDisabled: true } : {}),
    ...(options.defaultCollapsed ? { defaultCollapsed: true } : {}),
  };
}

function orderedLaneRows<TRow extends Row>(
  rows: readonly TableRowModel<TRow>[],
  rankField: string | undefined,
  optimisticPlacementByRowId: ReadonlyMap<string, BoardCardPlacement>,
): readonly TableRowModel<TRow>[] {
  if (!rankField) return rows;
  return [...rows].sort((left, right) => {
    const leftRank = displayedRank(left, rankField, optimisticPlacementByRowId);
    const rightRank = displayedRank(right, rankField, optimisticPlacementByRowId);
    if (leftRank === undefined) return rightRank === undefined ? 0 : 1;
    if (rightRank === undefined) return -1;
    return leftRank - rightRank;
  });
}

function displayedRank<TRow extends Row>(
  row: TableRowModel<TRow>,
  rankField: string,
  optimisticPlacementByRowId: ReadonlyMap<string, BoardCardPlacement>,
): number | undefined {
  const id = rowPublicId(row.original) ?? row.id;
  return optimisticPlacementByRowId.get(id)?.rank
    ?? finiteRank(readPath(row.original, rankField));
}

function undeclaredLaneLabel<TRow extends Row>(
  rows: readonly TableRowModel<TRow>[],
  laneSource: ResolvedBoardLaneSource,
  laneId: string,
  emptyValueLabel: string,
  unknownValueLabel: string,
): string {
  if (!laneId) return emptyValueLabel;
  for (const row of rows) {
    const option = relationSelectedOption(
      readPath(row.original, laneSource.field),
      laneSource.labelField ?? laneSource.relation.labelField,
    );
    if (option?.label) return option.label;
  }
  return unknownValueLabel;
}

function laneSourceNullable(laneSource: ResolvedBoardLaneSource): boolean {
  return laneSource.fieldMetadata.nullable === true;
}

function boardLaneDragEnabled(
  laneSource: ResolvedBoardLaneSource | null,
  modelMetadata: ModelMetadata | null | undefined,
): boolean {
  return laneSource
    ? fieldUpdatable(modelMetadata, laneSource.field)
      && (
        !laneSource.rankField
        || fieldUpdatable(modelMetadata, laneSource.rankField)
      )
    : false;
}

function finiteRank(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function ranksEqual(left: number | undefined, right: number | undefined): boolean {
  return left === right;
}
