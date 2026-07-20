import * as React from "react";
import { rowPublicId, type Row } from "@angee/metadata";

import type { RecordNavigation } from "./RecordPager";
import type {
  ListViewNavigationScope,
  ResourceListSnapshot,
} from "./resource-view-surface";
import { stableSerialize } from "./resource-view-model";

interface PendingRecordNavigation {
  page: number;
  edge: "first" | "last";
}

export interface UseListRecordNavigationOptions {
  /** Public id of the open record; null disables record navigation. */
  recordId?: string | null;
  /** Opens the selected neighboring record. */
  onSelect?: (id: string) => void;
  /** Changes the owning list page when no replayable snapshot scope exists yet. */
  onSetPage?: (page: number) => void;
}

export interface UseListRecordNavigationResult<TRow extends Row> {
  listState: ResourceListSnapshot<TRow> | null;
  navigationScope: ListViewNavigationScope | null;
  navigation: RecordNavigation | null;
  onListStateChange: (state: ResourceListSnapshot<TRow>) => void;
}

/**
 * Turn ListView snapshots into the standard record pager, including page-edge
 * transitions. The returned navigation scope can be replayed by an off-screen
 * List while a record surface replaces the visible collection.
 */
export function useListRecordNavigation<TRow extends Row>({
  recordId,
  onSelect,
  onSetPage,
}: UseListRecordNavigationOptions): UseListRecordNavigationResult<TRow> {
  const [listState, setListState] =
    React.useState<ResourceListSnapshot<TRow> | null>(null);
  const [navigationScope, setNavigationScope] =
    React.useState<ListViewNavigationScope | null>(null);
  const [pendingNavigation, setPendingNavigation] =
    React.useState<PendingRecordNavigation | null>(null);
  const listStateRef = React.useRef<ResourceListSnapshot<TRow> | null>(null);

  const onListStateChange = React.useCallback(
    (next: ResourceListSnapshot<TRow>) => {
      const current = listStateRef.current;
      if (shouldRetainListStateForRecordNavigation({ current, next, recordId })) {
        return;
      }
      listStateRef.current = next;
      setListState((currentState) =>
        listStatesEqual(currentState, next) ? currentState : next,
      );
      setNavigationScope((currentScope) =>
        navigationScopesEqual(currentScope, next.navigationScope ?? null)
          ? currentScope
          : (next.navigationScope ?? null),
      );
    },
    [recordId],
  );

  React.useEffect(() => {
    if (recordId) return;
    setPendingNavigation(null);
  }, [recordId]);

  React.useEffect(() => {
    if (!pendingNavigation || !listState || listState.fetching) return;
    if (pendingNavigation.page !== listState.page) return;

    const target =
      pendingNavigation.edge === "first"
        ? listState.rows[0]
        : listState.rows[listState.rows.length - 1];
    const targetId = rowPublicId(target);
    if (targetId) {
      setPendingNavigation(null);
      onSelect?.(targetId);
    } else if (listState.rows.length === 0) {
      setPendingNavigation(null);
    }
  }, [listState, onSelect, pendingNavigation]);

  const setPage = React.useCallback(
    (page: number) => {
      if (navigationScope) {
        setNavigationScope((current) =>
          current ? { ...current, page } : current,
        );
        return;
      }
      onSetPage?.(page);
    },
    [navigationScope, onSetPage],
  );

  const navigation = React.useMemo(
    () =>
      buildRecordNavigation({
        listState,
        recordId,
        onSelect,
        setPage,
        setPendingNavigation,
      }),
    [listState, onSelect, recordId, setPage],
  );

  return { listState, navigationScope, navigation, onListStateChange };
}

function buildRecordNavigation<TRow extends Row>({
  listState,
  recordId,
  onSelect,
  setPage,
  setPendingNavigation,
}: {
  listState: ResourceListSnapshot<TRow> | null;
  recordId?: string | null;
  onSelect?: (id: string) => void;
  setPage: (page: number) => void;
  setPendingNavigation: React.Dispatch<
    React.SetStateAction<PendingRecordNavigation | null>
  >;
}): RecordNavigation | null {
  if (typeof recordId !== "string" || !listState) return null;
  const index = listState.rows.findIndex((row) => rowPublicId(row) === recordId);
  if (index < 0) {
    return { total: listState.total ?? listState.rows.length };
  }

  const current = (listState.page - 1) * listState.pageSize + index + 1;
  const total = listState.total ?? Math.max(current, listState.rows.length);
  const prevId = rowPublicId(listState.rows[index - 1]);
  const nextId = rowPublicId(listState.rows[index + 1]);
  const canPrevPage = listState.hasPrev && listState.page > 1;
  const canNextPage =
    listState.hasNext
    && (listState.total === undefined || current < listState.total);

  return {
    current,
    total,
    onPrev:
      onSelect && prevId
        ? () => onSelect(prevId)
        : onSelect && canPrevPage
          ? () => {
              const page = Math.max(1, listState.page - 1);
              setPendingNavigation({ page, edge: "last" });
              setPage(page);
            }
          : undefined,
    onNext:
      onSelect && nextId
        ? () => onSelect(nextId)
        : onSelect && canNextPage
          ? () => {
              const page = listState.page + 1;
              setPendingNavigation({ page, edge: "first" });
              setPage(page);
            }
          : undefined,
  };
}

function listStatesEqual<TRow extends Row>(
  left: ResourceListSnapshot<TRow> | null,
  right: ResourceListSnapshot<TRow>,
): boolean {
  if (!left) return false;
  return (
    rowIdsEqual(left.rows, right.rows)
    && left.total === right.total
    && left.page === right.page
    && left.pageSize === right.pageSize
    && left.pageCount === right.pageCount
    && left.hasNext === right.hasNext
    && left.hasPrev === right.hasPrev
    && left.fetching === right.fetching
    && navigationScopesEqual(
      left.navigationScope ?? null,
      right.navigationScope ?? null,
    )
  );
}

function shouldRetainListStateForRecordNavigation<TRow extends Row>({
  current,
  next,
  recordId,
}: {
  current: ResourceListSnapshot<TRow> | null;
  next: ResourceListSnapshot<TRow>;
  recordId?: string | null;
}): boolean {
  if (!recordId || !current || !next.fetching) return false;
  if (!listStateHasRecord(current, recordId)) return false;
  return !listStateHasRecord(next, recordId);
}

function listStateHasRecord<TRow extends Row>(
  state: ResourceListSnapshot<TRow>,
  recordId: string,
): boolean {
  return state.rows.some((row) => rowPublicId(row) === recordId);
}

function navigationScopesEqual(
  left: ListViewNavigationScope | null,
  right: ListViewNavigationScope | null,
): boolean {
  if (left === right) return true;
  if (!left || !right) return false;
  return (
    left.page === right.page
    && left.pageSize === right.pageSize
    && stableSerialize(left.filter ?? null) === stableSerialize(right.filter ?? null)
    && stableSerialize(left.order ?? null) === stableSerialize(right.order ?? null)
  );
}

function rowIdsEqual(left: readonly Row[], right: readonly Row[]): boolean {
  if (left.length !== right.length) return false;
  return left.every(
    (row, index) => rowPublicId(row) === rowPublicId(right[index]),
  );
}
