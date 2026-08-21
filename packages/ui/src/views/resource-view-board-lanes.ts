import * as React from "react";
import {
  fieldUpdatable,
  refineResourceName,
  rowPublicId,
  type ModelMetadata,
  type Row,
} from "@angee/metadata";
import { useUpdate, type BaseRecord, type HttpError } from "@refinedev/core";
import type { Row as TableRowModel } from "@tanstack/react-table";

import { errorMessage, useToast } from "../feedback";
import { useUiT } from "../i18n";
import { useLatestRef } from "../lib/use-latest-ref";
import { relationValueId } from "../widgets/types";
import type { RelationOption } from "../widgets/RelationField";
import type { RelationFieldInfo } from "./model-metadata-defaults";
import {
  relationSelectedOption,
  useRelationOptions,
} from "./relation-options";
import { leafTableRows } from "./resource-view-codecs";
import { readPath, type RowGroup } from "./resource-view-list-body";
import type { BoardLaneSource } from "./resource-view-types";

type RowRecord = BaseRecord & Row;

export interface ResolvedBoardLaneSource extends BoardLaneSource {
  relation: RelationFieldInfo;
  fieldMetadata: NonNullable<ModelMetadata["fields"][string]>;
}

export interface BoardLaneState<TRow extends Row> {
  source: ResolvedBoardLaneSource | null;
  lanes: readonly RelationOption[];
  optimisticLaneByRowId: ReadonlyMap<string, string>;
  fetching: boolean;
  dragEnabled: boolean;
  onCardMove?: (row: TRow, laneId: string | null) => Promise<void>;
}

interface OptimisticLaneEntry {
  laneId: string;
  serverLaneAtMove: string;
  settled: boolean;
}

const EMPTY_OPTIMISTIC_LANE_ENTRIES: ReadonlyMap<string, OptimisticLaneEntry> =
  new Map();

export function useBoardLaneState<TRow extends Row>({
  laneSource,
  modelMetadata,
  rows,
  enabled,
}: {
  laneSource: ResolvedBoardLaneSource | null | undefined;
  modelMetadata: ModelMetadata | null | undefined;
  rows: readonly TRow[];
  enabled: boolean;
}): BoardLaneState<TRow> {
  const t = useUiT();
  const toast = useToast();
  const source = enabled ? (laneSource ?? null) : null;
  const laneResult = useRelationOptions(source?.relation ?? null, {
    labelField: source?.labelField,
    enabled: source !== null,
  });
  // Refine's optimistic cache writes a flat relation id, which conflicts with
  // rows that read nested relation objects; this map preserves the display lane.
  const [optimisticLaneEntryByRowId, setOptimisticLaneEntryByRowId] =
    React.useState<ReadonlyMap<string, OptimisticLaneEntry>>(
      EMPTY_OPTIMISTIC_LANE_ENTRIES,
    );
  const optimisticLaneByRowId = React.useMemo(
    () =>
      new Map(
        [...optimisticLaneEntryByRowId].map(([id, entry]) => [id, entry.laneId]),
      ),
    [optimisticLaneEntryByRowId],
  );
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
        if (
          optimisticLane !== undefined
          && optimisticLane.settled
          && (
            serverLane === optimisticLane.laneId
            || serverLane !== optimisticLane.serverLaneAtMove
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
    async (row: TRow, laneId: string | null): Promise<void> => {
      if (!dragEnabled || !source || !dataResource) return;
      if (laneId === null && !laneSourceNullable(source)) return;
      const id = rowPublicId(row);
      if (!id) return;
      const serverLane = relationValueId(readPath(row, source.field));
      const currentLane =
        optimisticLaneEntryByRowId.get(id)?.laneId ?? serverLane;
      const nextLane = laneId ?? "";
      if (currentLane === nextLane) return;
      setOptimisticLaneEntryByRowId((current) => {
        const next = new Map(current);
        next.set(id, {
          laneId: nextLane,
          serverLaneAtMove: serverLane,
          settled: false,
        });
        return next;
      });
      try {
        await updateRef.current.mutateAsync({
          id,
          values: { [source.field]: laneId },
        });
        setOptimisticLaneEntryByRowId((current) => {
          const entry = current.get(id);
          if (!entry || entry.laneId !== nextLane) return current;
          const next = new Map(current);
          next.set(id, { ...entry, settled: true });
          return next;
        });
      } catch (error) {
        setOptimisticLaneEntryByRowId((current) => {
          if (current.get(id)?.laneId !== nextLane) return current;
          const next = new Map(current);
          next.delete(id);
          return next;
        });
        toast.danger({
          title: t("board.moveFailed"),
          description: errorMessage(error, t("board.moveFailedDescription")),
        });
      }
    },
    [
      dataResource,
      dragEnabled,
      optimisticLaneEntryByRowId,
      source,
      t,
      toast,
      updateRef,
    ],
  );

  return React.useMemo(
    () => ({
      source,
      lanes: laneResult.options,
      optimisticLaneByRowId,
      fetching: laneResult.list.fetching,
      dragEnabled,
      ...(dragEnabled ? { onCardMove } : {}),
    }),
    [
      dragEnabled,
      laneResult.list.fetching,
      laneResult.options,
      onCardMove,
      optimisticLaneByRowId,
      source,
    ],
  );
}

export function rowGroupsFromLaneSource<TRow extends Row>(
  rows: readonly TableRowModel<TRow>[],
  laneSource: ResolvedBoardLaneSource,
  lanes: readonly RelationOption[],
  optimisticLaneByRowId: ReadonlyMap<string, string>,
  emptyValueLabel: string,
  unknownValueLabel: string,
): readonly RowGroup<TRow>[] {
  const leafRows = leafTableRows(rows);
  const rowsByLane = new Map<string, TableRowModel<TRow>[]>();
  const declaredLaneIds = new Set(lanes.map((lane) => lane.value));
  for (const row of leafRows) {
    const rowId = rowPublicId(row.original) ?? row.id;
    const laneId =
      optimisticLaneByRowId.get(rowId)
      ?? relationValueId(readPath(row.original, laneSource.field));
    const laneRows = rowsByLane.get(laneId);
    if (laneRows) laneRows.push(row);
    else rowsByLane.set(laneId, [row]);
  }

  const groups = lanes.map((lane) =>
    laneGroup(lane.value, lane.label, rowsByLane.get(lane.value) ?? []),
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
        laneRows,
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
  options: { dropDisabled?: boolean } = {},
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
  };
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
  return laneSource ? fieldUpdatable(modelMetadata, laneSource.field) : false;
}
