import * as React from "react";
import { rowPublicId, useModelMetadata, isClientRowModel, type Row } from "@angee/metadata";
import { stableSerialize } from "@angee/refine";
import { useResourceViewMaybe } from "./resource-view-context";
import { useResourceListQuery } from "./surface/resource-list-query";
import type { RecordNavigation } from "./RecordPager";
import type { ListViewNavigationScope, ResourceListSnapshot } from "./resource-view-surface";

interface PendingRecordNavigation {
  recordId: string;
  binding: string;
  sourceScope: string;
  scope: ListViewNavigationScope;
  edge: "first" | "last";
}
export interface UseListRecordNavigationOptions {
  resource: string;
  /** Public id of the open record; null disables the headless list query. */
  recordId?: string | null;
  /** Opens a neighboring record with the exact query scope that supplied it. */
  onSelect?: (id: string, scope?: ListViewNavigationScope) => void;
  /** Router-owned context. null explicitly disables context on a direct/invalid link. */
  navigationScope?: ListViewNavigationScope | null;
  /** Local row models retain their native table pagination owner. */
  onSetPage?: (page: number) => void;
  /** Select the first loaded row for this collection identity when none is active. */
  selectFirstRecord?: boolean;
  /** Identity of the collection whose first row should be selected. */
  firstSelectionKey?: string;
  /** Clear an active record when its collection becomes empty. */
  onClearSelection?: () => void;
}
export interface UseListRecordNavigationResult<TRow extends Row> {
  navigationScope: ListViewNavigationScope | null;
  /** Keep the one native local Table owner mounted while an inline record is open. */
  retainLocalList: boolean;
  navigation: RecordNavigation | null;
  onListStateChange: (state: ResourceListSnapshot<TRow>) => void;
  selectRecord: (id: string, scope?: ListViewNavigationScope) => void;
}

/**
 * The native Refine list cache owns server rows while the record replaces the
 * collection. Only query facts survive in local/Router state; no hidden List
 * (facets, grouping, controls, or duplicate row cache) is mounted for navigation.
 * Local row models keep their non-replayable native table snapshot instead.
 */
export function useListRecordNavigation<TRow extends Row>({
  resource, recordId, onSelect, navigationScope: controlledScope, onSetPage,
  selectFirstRecord = false, firstSelectionKey = "",
  onClearSelection,
}: UseListRecordNavigationOptions): UseListRecordNavigationResult<TRow> {
  const model = useModelMetadata(resource);
  const view = useResourceViewMaybe();
  const dataResource = model?.resource;
  const readId = React.useCallback((row: Row | null | undefined) => rowPublicId(row, dataResource), [dataResource]);
  const idFields = React.useMemo(() => [dataResource?.query.identity.field ?? "id"], [dataResource?.query.identity.field]);
  const localQueryScope = stableSerialize([view?.state.filter, view?.state.sorting, view?.state.groupStack, view?.state.pagination.pageSize, view?.state.view]);
  const binding = `${resource}:${model?.resource?.schemaName ?? ""}`;
  const [captured, setCaptured] = React.useState<{ binding: string; scope: ListViewNavigationScope | null } | null>(null);
  const scopeRef = React.useRef<{ binding: string; scope: ListViewNavigationScope } | null>(null);
  const [local, setLocal] = React.useState<{ binding: string; snapshot: ResourceListSnapshot<TRow> } | null>(null);
  const [localEdge, setLocalEdge] = React.useState<{ recordId: string; binding: string; sourceScope: string; page: number; edge: "first" | "last" } | null>(null);
  const [pending, setPending] = React.useState<PendingRecordNavigation | null>(null);
  const firstSelectionRef = React.useRef<string | null>(null);
  const navigationScope = isClientRowModel(model?.resource) ? null : controlledScope === undefined ? captured?.binding === binding ? captured.scope : null : controlledScope;
  const localSnapshot = local?.binding === binding && (controlledScope === undefined || isClientRowModel(model?.resource)) ? local.snapshot : null;
  const scopeIdentity = stableSerialize(navigationScope);
  const current = useResourceListQuery({ resource: model?.resource, scope: navigationScope, fields: idFields, enabled: Boolean(recordId) });
  const target = useResourceListQuery({ resource: model?.resource, scope: pending?.scope ?? null, fields: idFields, enabled: Boolean(recordId && pending?.recordId === recordId && pending.binding === binding && pending.sourceScope === scopeIdentity) });

  // Deciding INSIDE a setter is not enough. React only skips scheduling a
  // same-value update while the fiber has no pending lane; once one update has
  // landed on this component, every later `setX(prev => prev)` is scheduled,
  // renders, returns the same value, and still counts as a nested update. So the
  // comparison happens here, before the setter is called at all.
  const latestState = React.useRef({ captured, local });
  latestState.current = { captured, local };

  const onListStateChange = React.useCallback((state: ResourceListSnapshot<TRow>) => {
    const { captured: lastCaptured, local: lastLocal } = latestState.current;
    if (state.navigationScope) {
      if (lastLocal !== null) setLocal(null);
      scopeRef.current = { binding, scope: state.navigationScope };
      // A mounted drawer's collection cannot replace the record's independent page.
      const sameScope = lastCaptured?.binding === binding
        && stableSerialize(lastCaptured.scope) === stableSerialize(state.navigationScope);
      if (!recordId && !sameScope) {
        setCaptured({ binding, scope: state.navigationScope });
      }
    } else {
      scopeRef.current = null;
      if (!recordId && lastCaptured?.scope) setCaptured({ binding, scope: null });
      const snapshot = lastLocal?.binding === binding && state.fetching && !state.rows.some((row) => readId(row) === recordId)
        && lastLocal.snapshot.rows.some((row) => readId(row) === recordId)
        ? { ...lastLocal.snapshot, fetching: true }
        : state;
      const sameSnapshot = lastLocal?.binding === binding
        && stableSerialize(lastLocal.snapshot) === stableSerialize(snapshot);
      if (!sameSnapshot) setLocal({ binding, snapshot });
    }
    if (selectFirstRecord && !state.fetching) {
      const selectedStillExists = recordId
        ? state.rows.some((row) => readId(row) === recordId)
        : false;
      const nextId = selectedStillExists ? recordId : readId(state.rows[0]);
      const selectionIdentity = `${binding}:${firstSelectionKey}:${recordId ?? ""}:${nextId ?? ""}`;
      if (nextId !== recordId && firstSelectionRef.current !== selectionIdentity) {
        firstSelectionRef.current = selectionIdentity;
        if (nextId) onSelect?.(nextId, state.navigationScope ?? undefined);
        else onClearSelection?.();
      }
    }
  }, [binding, firstSelectionKey, onClearSelection, onSelect, recordId, readId, selectFirstRecord]);
  const selectRecord = React.useCallback((id: string, scope?: ListViewNavigationScope) => {
    const next = scope ?? (scopeRef.current?.binding === binding ? scopeRef.current.scope : null) ?? navigationScope;
    setCaptured({ binding, scope: next });
    onSelect?.(id, next ?? undefined);
  }, [binding, navigationScope, onSelect]);

  React.useEffect(() => {
    if (!pending) return;
    if (pending.recordId !== recordId || pending.binding !== binding || pending.sourceScope !== scopeIdentity || target.query.isError) { setPending(null); return; }
    if (target.query.isFetching || !target.query.isSuccess) return;
    const row = pending.edge === "first" ? target.result.data[0] : target.result.data.at(-1);
    const id = readId(row);
    setPending(null);
    if (id) selectRecord(id, pending.scope);
  }, [binding, pending, recordId, readId, scopeIdentity, selectRecord, target.query.isError, target.query.isFetching, target.query.isSuccess, target.result.data]);
  React.useEffect(() => {
    if (!localEdge) return;
    if (localEdge.recordId !== recordId || localEdge.binding !== binding || localEdge.sourceScope !== localQueryScope || localSnapshot?.error) { setLocalEdge(null); return; }
    if (!localSnapshot || localSnapshot.fetching || localSnapshot.page !== localEdge.page) return;
    const id = readId(localEdge.edge === "first" ? localSnapshot.rows[0] : localSnapshot.rows.at(-1));
    setLocalEdge(null);
    if (id) onSelect?.(id);
  }, [binding, localEdge, localQueryScope, localSnapshot, onSelect, recordId, readId]);

  let navigation: RecordNavigation | null = null;
  const rows = navigationScope ? current.result.data : localSnapshot?.rows;
  const total = navigationScope ? current.result.total : localSnapshot?.total;
  const page = navigationScope?.page ?? localSnapshot?.page ?? 1;
  const pageSize = navigationScope?.pageSize ?? localSnapshot?.pageSize ?? 1;
  const failed = navigationScope ? current.query.isError : Boolean(localSnapshot?.error);
  const index = recordId && rows ? rows.findIndex((row) => readId(row) === recordId) : -1;
  if (recordId && (navigationScope || localSnapshot) && !failed) {
    const position = index < 0 ? undefined : (page - 1) * pageSize + index + 1;
    navigation = { current: position, total };
    const ready = index >= 0 && onSelect && !pending && !localEdge && !(navigationScope ? current.query.isFetching : localSnapshot?.fetching);
    if (ready) {
      const move = (direction: -1 | 1) => {
        const id = readId(rows?.[index + direction]);
        if (id) { selectRecord(id, navigationScope ?? undefined); return; }
        const nextPage = page + direction;
        const edge = direction < 0 ? "last" : "first";
        if (navigationScope) setPending({ recordId, binding, sourceScope: scopeIdentity, scope: { ...navigationScope, page: nextPage }, edge });
        else if (onSetPage) { setLocalEdge({ recordId, binding, sourceScope: localQueryScope, page: nextPage, edge }); onSetPage(nextPage); }
      };
      if (index > 0 || (page > 1 && (navigationScope || onSetPage))) navigation.onPrev = () => move(-1);
      // Unknown totals never invent a page beyond the authoritative loaded slice.
      const hasNextPage = total !== undefined && page * pageSize < total;
      if (index < (rows?.length ?? 0) - 1 || (hasNextPage && (navigationScope || onSetPage))) navigation.onNext = () => move(1);
    }
  }
  return { navigationScope, retainLocalList: !navigationScope && Boolean(localSnapshot), navigation, onListStateChange, selectRecord };
}
