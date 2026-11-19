import {
  createContext,
  useCallback,
  useContext,
  useLayoutEffect,
  useMemo,
  useState,
  type Key,
  type ReactElement,
  type ReactNode,
} from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { functionalUpdate, type OnChangeFn, type PaginationState, type RowSelectionState, type SortingState, type Updater, type VisibilityState } from "@tanstack/react-table";
import { stableSerialize } from "@angee/refine";
import { Filter, ResourceQuery, useModelMetadata, type Row } from "@angee/metadata";
import { validateResourceViewState } from "./model/state";
import { normaliseGroupStack, normaliseSourceKeys } from "./model/search";
import { normalisePageSize } from "./page-size";
import { idsFromRowSelectionState } from "./resource-view-codecs";
import type { ResourceCollectionPresentation, SelectedGroup, SelectionGroupPath } from "./resource-view-types";
import { groupPathId } from "./resource-view-grouped-model";

import {
  createResourceViewState,
  type ResourceViewState,
  resourceViewSearchToState,
  resourceViewStateToSearch,
  mergeResourceViewSearch,
  type CalendarViewMode,
  type ResourceViewFavorite,
  type ResourceViewFilter,
  type ResourceViewGroup,
  type ResourceViewInitialState,
  type ResourceViewKind,
} from "./resource-view-model";
import { useAppRuntime } from "../../runtime";
import { useValueStable } from "../../lib/use-value-stable";
import { resourceViewPreset, resourceViewPresetDefaults } from "./model/favorites";
import { useResourceViewFavorites } from "./resource-view-favorites";

/** Group interaction state outlives a temporarily unmounted server list.
 * Resets are recorded during render so returning to a previous collection never
 * revives its pagination or expansion. */
export interface ResourceViewGroupExpansion {
  axisKey: string;
  collapsedKeys: ReadonlySet<string>;
  explicitExpandedKeys: ReadonlySet<string>;
  defaultExpandedKeys: ReadonlySet<string>;
}

type GroupPagination = Record<string, PaginationState>;
interface ResourceViewGroups {
  query: string;
  order: string;
  paginationByScope: GroupPagination;
  expansion: ResourceViewGroupExpansion | null;
}
const EMPTY_GROUP_PAGINATION: GroupPagination = {};
const EMPTY_PRESET_IDS: readonly string[] = [];
function groupsForQuery(current: ResourceViewGroups, query: string, order: string): ResourceViewGroups {
  if (current.query !== query) return { query, order, paginationByScope: EMPTY_GROUP_PAGINATION, expansion: null };
  // Sorting changes each bucket's record window, not the grouping tree itself.
  return current.order === order ? current : {
    ...current,
    order,
    paginationByScope: Object.fromEntries(Object.entries(current.paginationByScope).map(([key, pagination]) =>
      [key, { ...pagination, pageIndex: 0 }])),
  };
}

interface SelectionState {
  collection: string;
  scope: string;
  rowSelection: RowSelectionState;
  rows: Readonly<Record<string, Row>>;
  /** The group path captured with each snapshot; absent for a row selected without one. */
  paths: Readonly<Record<string, SelectionGroupPath>>;
}

type SelectionRead = Pick<SelectionState, "rowSelection" | "rows" | "paths">;
interface SelectionOwner {
  read: (scope: string) => SelectionRead;
  update: (
    scope: string,
    updater: Updater<RowSelectionState>,
    observed?: (id: string) => Row | undefined,
    placed?: (id: string) => SelectionGroupPath | undefined,
  ) => void;
  enter: (scope: string) => void;
}
const EMPTY_SELECTION: SelectionRead = { rowSelection: {}, rows: {}, paths: {} };
const SelectionOwnerContext = createContext<SelectionOwner | null>(null);
/** The selection scope of the nearest inherited view; the owner's own collection is "". */
const SelectionScopeContext = createContext("");

/** One public selection projection for the owning collection and inherited scopes. */
function useSelectionValue(scope: string, owner: SelectionOwner) {
  const { rowSelection, rows, paths } = owner.read(scope);
  const selectedRows = useMemo(() => [...idsFromRowSelectionState(rowSelection)]
    .flatMap((id) => {
      const row = rows[id];
      return row === undefined ? [] : [row];
    }), [rowSelection, rows]);
  const setRowSelection = useCallback<ResourceViewContextValue["setRowSelection"]>(
    (updater, observed, placed) => owner.update(scope, updater, observed, placed), [owner.update, scope],
  );
  const toggleSelectedId = useCallback<ResourceViewContextValue["toggleSelectedId"]>(
    (id, selected, row, path) => setRowSelection((current) => ({
      ...current, [id]: selected ?? !current[id],
    }), row === undefined ? undefined : (selectedId) => selectedId === id ? row : undefined,
    path === undefined ? undefined : (selectedId) => selectedId === id ? path : undefined),
    [setRowSelection],
  );
  const clearSelectedIds = useCallback(() => setRowSelection({}), [setRowSelection]);
  const selectedGroups = useCallback<ResourceViewContextValue["selectedGroups"]>(
    (grouping, placed) => selectedGroupsOf(rowSelection, paths, grouping, placed), [rowSelection, paths],
  );
  const value = useMemo(() => ({ selectedRows, setRowSelection, toggleSelectedId, clearSelectedIds, selectedGroups }),
    [selectedRows, setRowSelection, toggleSelectedId, clearSelectedIds, selectedGroups]);
  return { rowSelection, value };
}

/**
 * Split the selection by `grouping`, the levels the surface renders, from the paths
 * captured with the snapshots checked against `placed`, where the surface shows its
 * loaded rows now (null: it places none). See `bulkActions` for the three states.
 */
function selectedGroupsOf(
  rowSelection: RowSelectionState,
  paths: Readonly<Record<string, SelectionGroupPath>>,
  grouping: readonly ResourceViewGroup[],
  placed: ReadonlyMap<string, SelectionGroupPath | null> | null,
): readonly SelectedGroup[] | null | undefined {
  if (grouping.length === 0) return undefined;
  if (placed === null) return null;
  // Whole level specs in order: field, granularity and `repeated`.
  const levels = stableSerialize(grouping);
  const entries = new Map<string, { path: SelectionGroupPath; ids: string[] }>();
  for (const id of idsFromRowSelectionState(rowSelection)) {
    const captured = paths[id];
    const current = placed.get(id);
    // Never a partial or stale split: a row selected without a path or under other
    // levels, or loaded now under another group or under several, has no place.
    if (!captured || current === null || stableSerialize(captured.map(({ group }) => group)) !== levels) return null;
    const identity = groupPathId(captured);
    if (current !== undefined && groupPathId(current) !== identity) return null;
    const entry = entries.get(identity);
    if (!entry) entries.set(identity, { path: current ?? captured, ids: [id] });
    else {
      entry.ids.push(id);
      // A loaded row carries the group's current label and position.
      if (current) entry.path = current;
    }
  }
  return [...entries.values()]
    .sort((left, right) => {
      const depth = left.path.findIndex((level, index) => level.key !== right.path[index]!.key);
      return depth < 0 ? 0 : left.path[depth]!.position - right.path[depth]!.position;
    })
    .map(({ path, ids }) => {
      const levels = path.map(({ group, key, label }) => ({ field: group.field, key, label }));
      return { ...levels.at(-1)!, parents: levels.slice(0, -1), ids };
    });
}

/**
 * Selection survives pagination, page-size and view changes that keep the same
 * grouping. setView can install the new view's default groupStack, which clears
 * selection when it changes the grouping. Changes to the validated filter, sort,
 * groups, preset query/fixed filter, base filter or resource clear selection before
 * children render, including Router history changes. Only one scope holds a
 * selection: entering another inherited scope or selecting at the owner clears
 * the previous scope's selection.
 */
export interface ResourceViewContextValue {
  /** Resource whose collection state this provider owns. */
  resource?: string;
  /** Validated view state; rowSelection is the selection owner's live value for
   * this scope, replacing the providers' seed-only rowSelection field. */
  state: ResourceViewState;
  /** Declared route defaults resolved for the current view kind. */
  readonly defaultState: Readonly<ResourceViewState>;
  /** Effective immutable scope: caller base filter combined with the selected preset. */
  baseFilter?: ResourceViewFilter;
  setColumnVisibility: OnChangeFn<VisibilityState>;
  paginationByScope: GroupPagination;
  setPaginationByScope: OnChangeFn<GroupPagination>;
  groupExpansion: ResourceViewGroupExpansion | null;
  setGroupExpansion: OnChangeFn<ResourceViewGroupExpansion | null>;
  setPage: (page: number) => void;
  setPageSize: (pageSize: number) => void;
  setPagination: OnChangeFn<PaginationState>;
  setSorting: OnChangeFn<SortingState>;
  /** Writes apply to the last rendered collection. A collection change in the
   * same batch clears the write. Observed rows, and the group path a grouped
   * surface placed each in, are captured only on selection. */
  setRowSelection: (
    updater: Updater<RowSelectionState>,
    observed?: (id: string) => Row | undefined,
    placed?: (id: string) => SelectionGroupPath | undefined,
  ) => void;
  /** Rows observed when selected, in selection-map (object key) order.
   * Initial/programmatic ids without a row are omitted. Snapshots stay unchanged
   * until deselection. Actions needing fresh rows must validate them, for example
   * by revision; reselecting captures the current row. Every surface in the scope
   * shares these snapshots, so a row may carry the fields of whichever surface
   * selected it. */
  selectedRows: readonly Row[];
  /** The selection split by `grouping`, the levels the surface renders, from the
   * group paths captured with the snapshots and checked against `placed`, where it
   * shows its loaded rows now; null when the surface places no rows
   * (`bulkActions` → `selectedGroups`). */
  selectedGroups: (
    grouping: readonly ResourceViewGroup[],
    placed: ReadonlyMap<string, SelectionGroupPath | null> | null,
  ) => readonly SelectedGroup[] | null | undefined;
  setFilter: OnChangeFn<ResourceViewFilter>;
  resetQuery: () => void;
  /** Whether editable query state differs from this collection's default view. */
  queryDirty: boolean;
  /** Restore the collection's default query, including its route preset. */
  clearQuery: () => void;
  /** Remove a selected shipped preset, including its immutable fixed filter. */
  clearPreset: () => void;
  setGroupStack: (groupStack: readonly ResourceViewGroup[]) => void;
  toggleSelectedId: (id: string, selected?: boolean, row?: Row, path?: SelectionGroupPath) => void;
  clearSelectedIds: () => void;
  setView: (view: ResourceViewKind) => void;
  setMode: (mode: CalendarViewMode) => void;
  setAnchor: (anchor: string) => void;
  /** Hide the calendar sources with these keys (the rest show). */
  setHiddenSources: (keys: readonly string[]) => void;
  savedFavorites: readonly ResourceViewFavorite[];
  saveFavorite?: (label: string) => void;
  renameFavorite?: (id: string, label: string) => void;
  pinFavorite?: (id: string, pinned: boolean) => void;
  applyFavorite: (favorite: ResourceViewFavorite) => void;
}

export interface ResourceViewProviderProps {
  children: ReactNode;
  initialState?: ResourceViewInitialState;
  resource?: string;
  baseFilter?: ResourceViewFilter;
  /** Stable authored-collection identity for favorites when there is no model resource. */
  favoriteKey?: string;
  /** Shipped presets declared by this collection route, besides its route default. */
  presetIds?: readonly string[];
  scope?: ResourceViewProviderScope;
  /** Isolate this collection's URL keys when a page contains several collections. */
  namespace?: string;
}

export type ResourceViewProviderScope = "route" | "local";

export interface ResourceViewScopeMountOptions {
  ambient: ResourceViewContextValue | null;
  resource?: string;
  /** Typed resource filters are validated at this shared view boundary. */
  baseFilter?: unknown;
  scope?: "inherit" | "local";
  /** Embedded collections own local state unless the caller explicitly opts in
   * to an ambient or route-owned view. */
  presentation?: ResourceCollectionPresentation;
  initialState?: ResourceViewInitialState;
  isolated?: boolean;
  providerKey?: Key;
  presetIds?: readonly string[];
  children: (resourceView: ResourceViewContextValue) => ReactElement;
}

const ResourceViewContext = createContext<ResourceViewContextValue | null>(null);
type ResourceViewNavigate = (options: {
  search: (current: Record<string, unknown>) => Record<string, unknown>;
  replace?: boolean;
  state: true;
}) => Promise<void> | void;

/**
 * @kind null
 * @reason Provider infrastructure: ResourceView context.
 */
export function ResourceViewProvider({
  children,
  initialState,
  resource,
  baseFilter,
  favoriteKey,
  presetIds,
  scope = "route",
  namespace,
}: ResourceViewProviderProps): ReactNode {
  if (scope === "local") {
    return (
      <LocalResourceViewProvider
        initialState={initialState}
        resource={resource}
        baseFilter={baseFilter}
        favoriteKey={favoriteKey}
        presetIds={presetIds}
      >
        {children}
      </LocalResourceViewProvider>
    );
  }
  return (
    <RouteResourceViewProvider
      initialState={initialState}
      resource={resource}
      baseFilter={baseFilter}
      favoriteKey={favoriteKey}
      presetIds={presetIds}
      namespace={namespace}
    >
      {children}
    </RouteResourceViewProvider>
  );
}

/** Mount under an ambient view when allowed, otherwise create the one state owner.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function withResourceViewScope({
  ambient,
  resource,
  baseFilter,
  scope,
  presentation,
  initialState,
  isolated = false,
  providerKey,
  presetIds,
  children,
}: ResourceViewScopeMountOptions): ReactElement {
  const resolvedScope = scope ?? (presentation === "embedded" ? "local" : "inherit");
  if (!isolated && resolvedScope !== "local" && ambient) {
    if (baseFilter === undefined) return <InheritedResourceView ambient={ambient}>{children}</InheritedResourceView>;
    return <ScopedResourceView ambient={ambient} baseFilter={baseFilter}>{children}</ScopedResourceView>;
  }
  return (
    <ResourceViewProvider
      key={providerKey}
      initialState={initialState}
      resource={resource}
      baseFilter={baseFilter === undefined ? undefined : Filter.from(baseFilter).value}
      scope={isolated || resolvedScope === "local" ? "local" : "route"}
      presetIds={presetIds}
    >
      <ResourceViewScopeBound>{children}</ResourceViewScopeBound>
    </ResourceViewProvider>
  );
}

/** An inherited view without its own base filter re-enters the ambient view's scope. */
function InheritedResourceView({ ambient, children }: {
  ambient: ResourceViewContextValue;
  children: ResourceViewScopeMountOptions["children"];
}): ReactElement {
  const owner = useContext(SelectionOwnerContext);
  const scopeKey = useContext(SelectionScopeContext);
  const enter = owner?.enter;
  // Recorded like a nested scope's entry, so X → unscoped → X never revives X's ids.
  useLayoutEffect(() => enter?.(scopeKey), [enter, scopeKey]);
  return children(ambient);
}

/** Record scope transitions before paint, even when the next scope selects nothing. */
function ScopedResourceView({ ambient, baseFilter, children }: {
  ambient: ResourceViewContextValue;
  baseFilter: unknown;
  children: ResourceViewScopeMountOptions["children"];
}): ReactElement {
  const owner = useContext(SelectionOwnerContext);
  if (!owner) throw new Error("An inherited resource view requires a selection owner.");
  const scopedBaseFilter = useMemo(() => Filter.combineOptional(ambient.baseFilter, baseFilter),
    [ambient.baseFilter, baseFilter]);
  const scopeKey = stableSerialize(scopedBaseFilter);
  const { rowSelection, value: selection } = useSelectionValue(scopeKey, owner);
  useLayoutEffect(() => owner.enter(scopeKey), [owner.enter, scopeKey]);
  const state = ambient.state;
  const viewState = useMemo(() => ({ ...state, rowSelection }), [state, rowSelection]);
  const scoped = useMemo<ResourceViewContextValue>(() => ({
    ...ambient,
    ...selection,
    baseFilter: scopedBaseFilter,
    state: viewState,
  }), [ambient, selection, scopedBaseFilter, viewState]);
  return (
    <SelectionScopeContext.Provider value={scopeKey}>
      <ResourceViewContext.Provider value={scoped}>{children(scoped)}</ResourceViewContext.Provider>
    </SelectionScopeContext.Provider>
  );
}

function ResourceViewScopeBound({
  children,
}: {
  children: (resourceView: ResourceViewContextValue) => ReactElement;
}): ReactElement {
  return children(useResourceView());
}

function RouteResourceViewProvider({
  children,
  initialState,
  resource,
  baseFilter,
  favoriteKey,
  presetIds = EMPTY_PRESET_IDS,
  namespace,
}: Omit<ResourceViewProviderProps, "scope">): ReactNode {
  const search = useSearch({ strict: false });
  const { resourceViews, defaultResourceView, menuResourceViewIds } = useAppRuntime();
  const model = useModelMetadata(resource ?? "");
  const modelLabel = model?.resource.modelLabel ?? resource;
  const routePreset = defaultResourceView && resourceViews[defaultResourceView]?.resource === modelLabel
    ? defaultResourceView : undefined;
  const declaredPresetIds = useValueStable(presetIds);
  const menuPresetIds = useValueStable(menuResourceViewIds ?? EMPTY_PRESET_IDS);
  const initialPreset = initialState?.preset;
  const allowedPresetIds = useMemo(
    () => [...new Set([...(routePreset ? [routePreset] : []), ...declaredPresetIds, ...menuPresetIds, ...(initialPreset ? [initialPreset] : [])])],
    [routePreset, declaredPresetIds, menuPresetIds, initialPreset],
  );
  const defaultsFor = useCallback((id: string | undefined) => {
    const preset = resourceViewPreset(resourceViews, id, modelLabel, allowedPresetIds);
    return resourceViewPresetDefaults(initialState, preset);
  }, [initialState, resourceViews, modelLabel, allowedPresetIds]);
  const presetKey = namespace ? `${namespace}.preset` : "preset";
  const readState = useCallback((current: Record<string, unknown>) => {
    try {
      const id = current[presetKey] ?? routePreset;
      if (id !== undefined && typeof id !== "string") throw new Error("Invalid resource view name.");
      return resourceViewSearchToState(current, defaultsFor(id), namespace);
    } catch (error) {
      return { ...createResourceViewState(initialState), queryError: error instanceof Error ? error : new Error("Invalid resource view.") };
    }
  }, [defaultsFor, initialState, namespace, presetKey, routePreset]);
  // Narrow Router navigation to functional search updates; no from is supplied
  // because the updater is route-agnostic.
  const navigate = useNavigate() as ResourceViewNavigate;
  const queryState = useMemo(
    () => readState(search),
    [search, readState],
  );
  const [failedTransition, setFailedTransition] = useState<{
    search: unknown;
    error: Error;
  } | null>(null);
  const transitionError =
    failedTransition && failedTransition.search === search
      ? failedTransition.error
      : null;
  const state = useMemo(
    () => ({
      ...queryState,
      queryError: transitionError ?? queryState.queryError,
    }),
    [queryState, transitionError],
  );
  // ResourceViewState requires rowSelection here, but it is seed-only query
  // state. Query updaters must not read it; the shared owner exposes live ids.
  const updateState = useCallback<OnChangeFn<ResourceViewState>>(
    (updater) => {
      const next = functionalUpdate(updater, queryState);
      if (next.queryError) {
        setFailedTransition({ search, error: next.queryError });
        return;
      }
      setFailedTransition(null);
      void navigate({
        state: true,
        search: (current) => {
          const updated = functionalUpdate(
            updater,
            readState(current),
          );
          return updated.queryError
            ? current
            : mergeResourceViewSearch(
                current,
                {
                  ...resourceViewStateToSearch(updated, defaultsFor(updated.preset)),
                  preset: updated.preset === routePreset ? undefined : updated.preset,
                },
                namespace,
              );
        },
        replace: true,
      });
    },
    [defaultsFor, readState, routePreset, navigate, namespace, queryState, search],
  );
  const defaultState = useMemo(
    () => createResourceViewState({ ...defaultsFor(routePreset), view: state.view }),
    [defaultsFor, routePreset, state.view],
  );
  const { value, selectionOwner } = useResourceViewContextValue({
    updateState,
    resource,
    baseFilter,
    favoriteKey,
    presetIds: allowedPresetIds,
    defaultState,
    initialState,
    state,
  });

  return <ResourceViewOwner value={value} selectionOwner={selectionOwner}>{children}</ResourceViewOwner>;
}

/** Publish an owner's view and selection; its own collection is selection scope "". */
function ResourceViewOwner({ value, selectionOwner, children }: {
  value: ResourceViewContextValue;
  selectionOwner: SelectionOwner;
  children: ReactNode;
}): ReactElement {
  return (
    <SelectionOwnerContext.Provider value={selectionOwner}>
      <SelectionScopeContext.Provider value="">
        <ResourceViewContext.Provider value={value}>
          {children}
        </ResourceViewContext.Provider>
      </SelectionScopeContext.Provider>
    </SelectionOwnerContext.Provider>
  );
}

function LocalResourceViewProvider({
  children,
  initialState,
  resource,
  baseFilter,
  favoriteKey,
  presetIds,
}: Omit<ResourceViewProviderProps, "scope">): ReactNode {
  const declaredPresetIds = useValueStable(presetIds);
  const initialPreset = initialState?.preset;
  const allowedPresetIds = useMemo(
    () => declaredPresetIds === undefined ? undefined : [...new Set([...declaredPresetIds, ...(initialPreset ? [initialPreset] : [])])],
    [declaredPresetIds, initialPreset],
  );
  // Keep the required rowSelection seed in this query state; only the shared
  // owner reads/writes live selection, never query-state updaters.
  const [state, updateState] = useState(() =>
    createResourceViewState(initialState),
  );
  const scope = stableSerialize([resource, baseFilter]);
  const [previousScope, setPreviousScope] = useState(scope);
  if (previousScope !== scope) {
    // An external collection change starts a new window, retaining the user's
    // search, ordering and page size. Reconcile before children issue reads.
    setPreviousScope(scope);
    updateState({ ...state, pagination: { ...state.pagination, pageIndex: 0 } });
  }
  const defaultState = useMemo(() => createResourceViewState({ ...initialState, view: state.view }), [initialState, state.view]);
  const { value, selectionOwner } = useResourceViewContextValue({
    updateState,
    resource,
    baseFilter,
    favoriteKey,
    presetIds: allowedPresetIds,
    defaultState,
    initialState,
    state,
  });

  return <ResourceViewOwner value={value} selectionOwner={selectionOwner}>{children}</ResourceViewOwner>;
}

function useResourceViewContextValue({
  updateState,
  resource,
  baseFilter,
  favoriteKey,
  presetIds,
  defaultState,
  initialState,
  state: sourceState,
}: {
  updateState: OnChangeFn<ResourceViewState>;
  resource: string | undefined;
  baseFilter?: ResourceViewFilter;
  favoriteKey?: string;
  presetIds?: readonly string[];
  defaultState: ResourceViewState;
  initialState?: ResourceViewInitialState;
  state: ResourceViewState;
}): { value: ResourceViewContextValue; selectionOwner: SelectionOwner } {
  const metadata = useModelMetadata(resource ?? "");
  const { resourceViews } = useAppRuntime();
  const preset = sourceState.preset ? resourceViews[sourceState.preset] : undefined;
  const effectiveBaseFilter = useMemo(
    () => Filter.combineOptional(baseFilter, preset?.fixedFilter),
    [baseFilter, preset?.fixedFilter],
  );
  const state = useMemo(() => {
    try {
      resourceViewPreset(resourceViews, sourceState.preset, metadata?.resource.modelLabel ?? resource, presetIds);
      if (!metadata) return sourceState;
      const query = ResourceQuery.from(metadata);
      query.filterFrom(effectiveBaseFilter);
      return validateResourceViewState(sourceState, query);
    } catch (error) {
      return { ...sourceState, queryError: error instanceof Error ? error : new Error("Invalid resource view.") };
    }
  }, [metadata, resource, sourceState, resourceViews, effectiveBaseFilter, presetIds]);
  const queryDirty = state.preset !== defaultState.preset
    || stableSerialize(state.filter) !== stableSerialize(defaultState.filter)
    || stableSerialize(state.sorting ?? []) !== stableSerialize(defaultState.sorting ?? [])
    || stableSerialize(state.groupStack) !== stableSerialize(defaultState.groupStack);
  const { savedFavorites, saveFavorite, renameFavorite, pinFavorite } = useResourceViewFavorites(
    resource,
    state,
    favoriteKey,
    presetIds,
  );
  // Query facts belong to ResourceView; an external Router change must discard
  // old group interaction state before any newly mounted surface starts reads.
  const groupQuery = stableSerialize([
    resource,
    state.filter,
    effectiveBaseFilter,
    state.groupStack,
  ]);
  const groupOrder = stableSerialize(state.sorting);
  const [groups, setGroups] = useState<ResourceViewGroups>(() => ({
    query: groupQuery,
    order: groupOrder,
    paginationByScope: EMPTY_GROUP_PAGINATION,
    expansion: null,
  }));
  const activeGroups = groupsForQuery(groups, groupQuery, groupOrder);
  if (activeGroups !== groups) setGroups(activeGroups);

  const collection = stableSerialize([groupQuery, groupOrder]);
  const [selection, setSelection] = useState<SelectionState>(() => ({
    collection, scope: "", rowSelection: createResourceViewState(initialState).rowSelection, rows: {}, paths: {},
  }));
  const activeSelection = selection.collection === collection ? selection : {
    collection, scope: "", ...EMPTY_SELECTION,
  };
  // Adjust during render: children receive the empty value on this render too.
  if (activeSelection !== selection) setSelection(activeSelection);
  const read = useCallback<SelectionOwner["read"]>((scope) =>
    activeSelection.scope === scope ? activeSelection : EMPTY_SELECTION, [activeSelection]);
  const update = useCallback<SelectionOwner["update"]>((scope, updater, observed, placed) => {
    setSelection((current) => {
      const base = current.scope === scope ? current : EMPTY_SELECTION;
      const rowSelection = functionalUpdate(updater, base.rowSelection);
      // Object key order is part of the selectedRows contract.
      const ids = Object.keys(rowSelection);
      const previousIds = Object.keys(base.rowSelection);
      if (ids.length === previousIds.length && ids.every((id, index) =>
        id === previousIds[index] && rowSelection[id] === base.rowSelection[id])) return current;
      const selectedIds = idsFromRowSelectionState(rowSelection);
      // Empty writes neither change an empty selection nor displace another scope.
      if (selectedIds.size === 0 && (current.scope !== scope
        || !previousIds.some((id) => base.rowSelection[id]))) return current;
      const rows: Record<string, Row> = {};
      const paths: Record<string, SelectionGroupPath> = {};
      for (const id of selectedIds) {
        const kept = Boolean(base.rowSelection[id]);
        const row = kept ? base.rows[id] : observed?.(id);
        if (row !== undefined) rows[id] = row;
        const path = kept ? base.paths[id] : placed?.(id);
        if (path !== undefined) paths[id] = path;
      }
      return { ...current, scope, rowSelection, rows, paths };
    });
  }, []);
  const enter = useCallback<SelectionOwner["enter"]>((scope) => {
    setSelection((current) => current.scope === scope ? current : { ...current, scope, ...EMPTY_SELECTION });
  }, []);
  const selectionOwner = useMemo(() => ({ read, update, enter }), [read, update, enter]);
  const { rowSelection, value: { selectedRows, setRowSelection, toggleSelectedId, clearSelectedIds, selectedGroups } } = useSelectionValue("", selectionOwner);
  const viewState = useMemo(() => ({ ...state, rowSelection }), [state, rowSelection]);
  const setPaginationByScope = useCallback<OnChangeFn<GroupPagination>>(
    (updater) => {
      setGroups((current) => {
        const base = groupsForQuery(current, groupQuery, groupOrder);
        const paginationByScope = functionalUpdate(
          updater,
          base.paginationByScope,
        );
        return base === current && paginationByScope === base.paginationByScope
          ? current
          : { ...base, paginationByScope };
      });
    },
    [groupQuery, groupOrder],
  );
  const setGroupExpansion = useCallback<
    OnChangeFn<ResourceViewGroupExpansion | null>
  >(
    (updater) => {
      setGroups((current) => {
        const base = groupsForQuery(current, groupQuery, groupOrder);
        const expansion = functionalUpdate(updater, base.expansion);
        return base === current && expansion === base.expansion
          ? current
          : { ...base, expansion };
      });
    },
    [groupQuery, groupOrder],
  );
  const resetScope = useCallback<OnChangeFn<ResourceViewState>>(
    (updater) => {
      updateState((current) => {
        const next = functionalUpdate(updater, current);
        return {
          ...next,
          pagination: { ...next.pagination, pageIndex: 0 },
        };
      });
    },
    [updateState],
  );
  const setPagination = useCallback<OnChangeFn<PaginationState>>(
    (updater) => {
      updateState((current) => {
        const next = functionalUpdate(updater, current.pagination);
        const sizeChanged = next.pageSize !== current.pagination.pageSize;
        return {
          ...current,
          pagination: {
            pageIndex: sizeChanged
              ? 0
              : Math.max(
                  0,
                  Number.isFinite(next.pageIndex)
                    ? Math.floor(next.pageIndex)
                    : 0,
                ),
            pageSize: normalisePageSize(next.pageSize),
          },
        };
      });
    },
    [updateState],
  );
  const setSorting = useCallback<OnChangeFn<SortingState>>(
    (updater) => {
      resetScope((current) => ({
        ...current,
        sorting: functionalUpdate(updater, current.sorting ?? []),
      }));
    },
    [resetScope],
  );
  const setGroupStack = useCallback(
    (groups: readonly ResourceViewGroup[]) => {
      const groupStack = normaliseGroupStack(groups);
      resetScope((current) => ({ ...current, groupStack }));
    },
    [resetScope],
  );
  const value = useMemo<ResourceViewContextValue>(
    () => ({
      resource,
      state: viewState,
      selectedRows,
      selectedGroups,
      setRowSelection,
      toggleSelectedId,
      clearSelectedIds,
      defaultState,
      baseFilter: effectiveBaseFilter,
      setColumnVisibility: (updater: Parameters<OnChangeFn<VisibilityState>>[0]) => updateState((current) => ({
        ...current, columnVisibility: functionalUpdate(updater, current.columnVisibility),
      })),
      paginationByScope: activeGroups.paginationByScope,
      setPaginationByScope,
      groupExpansion: activeGroups.expansion,
      setGroupExpansion,
      savedFavorites,
      saveFavorite,
      renameFavorite,
      pinFavorite,
      setPagination,
      setSorting,
      setPage: (page: number) =>
        setPagination((current) => ({ ...current, pageIndex: page - 1 })),
      setPageSize: (pageSize: number) =>
        setPagination((current) => ({ ...current, pageSize })),
      setFilter: (filter: Updater<ResourceViewFilter>) =>
        resetScope((current) => ({ ...current, filter: functionalUpdate(filter, current.filter), queryError: null })),
      resetQuery: () =>
        resetScope((current) => ({
          ...current,
          filter: {},
          sorting: [],
          groupStack: [],
          queryError: null,
        })),
      queryDirty,
      clearQuery: () =>
        resetScope((current) => ({
          ...current,
          preset: defaultState.preset,
          filter: defaultState.filter,
          sorting: defaultState.sorting,
          groupStack: defaultState.groupStack,
          queryError: null,
        })),
      clearPreset: () =>
        resetScope((current) => ({
          ...current,
          preset: "",
          filter: {},
          sorting: [],
          groupStack: [],
          queryError: null,
        })),
      setGroupStack,
      setView: (view: ResourceViewKind) =>
        updateState((current) => {
          if (view === current.view) return current;
          const defaults = resourceViewPresetDefaults(initialState,
            resourceViewPreset(resourceViews, current.preset, metadata?.resource.modelLabel ?? resource, presetIds));
          const atDefault = current.groupStack.length === 0 || stableSerialize(current.groupStack)
            === stableSerialize(createResourceViewState({ ...defaults, view: current.view }).groupStack);
          return { ...current, view, groupStack: atDefault
            ? createResourceViewState({ ...defaults, view }).groupStack : current.groupStack };
        }),
      setMode: (mode: CalendarViewMode) =>
        updateState((current) => ({ ...current, mode })),
      setAnchor: (anchor: string) =>
        updateState((current) => ({ ...current, anchor })),
      setHiddenSources: (keys: readonly string[]) =>
        updateState((current) => ({ ...current, hiddenSources: normaliseSourceKeys(keys) })),
      applyFavorite: (favorite: ResourceViewFavorite) =>
        resetScope((current) => {
          try {
            const presetId = favorite.preset ?? current.preset;
            resourceViewPreset(resourceViews, presetId, metadata?.resource.modelLabel ?? resource, presetIds);
            const favoriteState = createResourceViewState({
              ...favorite,
              preset: presetId,
              filter: Filter.from(favorite.filter).value,
              groupStack: normaliseGroupStack(favorite.groupStack ?? []),
              sort: favorite.sort ?? null,
              mode: current.mode,
              anchor: current.anchor,
              // A view that names no hidden sources leaves the calendar's toggles as they are.
              hiddenSources: favorite.hiddenSources ?? current.hiddenSources,
            });
            return {
              ...current,
              ...favoriteState,
              queryError: null,
            };
          } catch (error) {
            return {
              ...current,
              queryError:
                error instanceof Error
                  ? error
                  : new Error("Invalid saved query."),
            };
          }
        }),
    }),
    [
      resource,
      viewState,
      selectedRows,
      selectedGroups,
      setRowSelection,
      toggleSelectedId,
      clearSelectedIds,
      defaultState,
      initialState,
      queryDirty,
      preset,
      effectiveBaseFilter,
      resourceViews,
      presetIds,
      metadata,
      activeGroups.paginationByScope,
      activeGroups.expansion,
      setPaginationByScope,
      setGroupExpansion,
      savedFavorites,
      saveFavorite,
      renameFavorite,
      pinFavorite,
      setPagination,
      setSorting,
      resetScope,
      setGroupStack,
      updateState,
    ],
  );
  return { value, selectionOwner };
}

/**
 * @owner ResourceViewProvider
 */
export function useResourceView(): ResourceViewContextValue {
  const value = useContext(ResourceViewContext);
  if (!value) {
    throw new Error("useResourceView must be used under ResourceViewProvider.");
  }
  return value;
}

/**
 * @owner ResourceViewProvider
 */
export function useResourceViewMaybe(): ResourceViewContextValue | null {
  return useContext(ResourceViewContext);
}
