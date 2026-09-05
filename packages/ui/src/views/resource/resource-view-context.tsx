import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type Key,
  type ReactElement,
  type ReactNode,
} from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { functionalUpdate, type OnChangeFn, type PaginationState, type RowSelectionState, type SortingState } from "@tanstack/react-table";
import { clampPageSize, stableSerialize } from "@angee/refine";
import { normaliseGroupStack } from "./model/search";

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
import { useResourceViewFavorites } from "./resource-view-favorites";

/** Group interaction state outlives a temporarily unmounted server list. */
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

export interface ResourceViewContextValue {
  state: ResourceViewState;
  paginationByScope: GroupPagination;
  setPaginationByScope: OnChangeFn<GroupPagination>;
  groupExpansion: ResourceViewGroupExpansion | null;
  setGroupExpansion: OnChangeFn<ResourceViewGroupExpansion | null>;
  setPage: (page: number) => void;
  setPageSize: (pageSize: number) => void;
  setPagination: OnChangeFn<PaginationState>;
  setSorting: OnChangeFn<SortingState>;
  setRowSelection: OnChangeFn<RowSelectionState>;
  setFilter: (filter: ResourceViewFilter) => void;
  setGroup: (group: ResourceViewGroup | null) => void;
  setGroupStack: (groupStack: readonly ResourceViewGroup[]) => void;
  toggleSelectedId: (id: string, selected?: boolean) => void;
  clearSelectedIds: () => void;
  setView: (view: ResourceViewKind) => void;
  setMode: (mode: CalendarViewMode) => void;
  setAnchor: (anchor: string) => void;
  savedFavorites: readonly ResourceViewFavorite[];
  saveFavorite?: (label: string) => void;
  applyFavorite: (favorite: ResourceViewFavorite) => void;
}

export interface ResourceViewProviderProps {
  children: ReactNode;
  initialState?: ResourceViewInitialState;
  resource?: string;
  scope?: ResourceViewProviderScope;
}

export type ResourceViewProviderScope = "route" | "local";

export interface ResourceViewScopeMountOptions {
  ambient: ResourceViewContextValue | null;
  resource?: string;
  scope: "inherit" | "local";
  initialState?: ResourceViewInitialState;
  isolated?: boolean;
  providerKey?: Key;
  children: (resourceView: ResourceViewContextValue) => ReactElement;
}

const ResourceViewContext = createContext<ResourceViewContextValue | null>(null);
type ResourceViewNavigate = (options: {
  search: (current: Record<string, unknown>) => Record<string, unknown>;
  replace?: boolean;
}) => Promise<void> | void;

export function ResourceViewProvider({
  children,
  initialState,
  resource,
  scope = "route",
}: ResourceViewProviderProps): ReactNode {
  if (scope === "local") {
    return (
      <LocalResourceViewProvider initialState={initialState} resource={resource}>
        {children}
      </LocalResourceViewProvider>
    );
  }
  return (
    <RouteResourceViewProvider initialState={initialState} resource={resource}>
      {children}
    </RouteResourceViewProvider>
  );
}

/** Mount under an ambient view when allowed, otherwise create the one state owner. */
export function withResourceViewScope({
  ambient,
  resource,
  scope,
  initialState,
  isolated = false,
  providerKey,
  children,
}: ResourceViewScopeMountOptions): ReactElement {
  if (!isolated && scope !== "local" && ambient) return children(ambient);
  return (
    <ResourceViewProvider
      key={providerKey}
      initialState={initialState}
      resource={resource}
      scope={isolated || scope === "local" ? "local" : "route"}
    >
      <ResourceViewScopeBound>{children}</ResourceViewScopeBound>
    </ResourceViewProvider>
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
}: Omit<ResourceViewProviderProps, "scope">): ReactNode {
  const search = useSearch({ strict: false });
  // Narrow Router navigation to functional search updates; no from is supplied
  // because the updater is route-agnostic.
  const navigate = useNavigate() as ResourceViewNavigate;
  const [rowSelection, setRowSelection] = useState<RowSelectionState>(
    () => createResourceViewState(initialState).rowSelection,
  );
  const queryState = useMemo(
    () => resourceViewSearchToState(search, initialState),
    [search, initialState],
  );
  const state = useMemo(() => ({ ...queryState, rowSelection }), [queryState, rowSelection]);
  const updateState = useCallback<OnChangeFn<ResourceViewState>>((updater) => {
    void navigate({
      search: (current) => mergeResourceViewSearch(
        current,
        resourceViewStateToSearch(functionalUpdate(updater, resourceViewSearchToState(current, initialState)), initialState),
      ),
      replace: true,
    });
  }, [initialState, navigate]);
  const value = useResourceViewContextValue({ updateState, setRowSelection, resource, state });

  return (
    <ResourceViewContext.Provider value={value}>
      {children}
    </ResourceViewContext.Provider>
  );
}

function LocalResourceViewProvider({
  children,
  initialState,
  resource,
}: Omit<ResourceViewProviderProps, "scope">): ReactNode {
  const [state, updateState] = useState(() => createResourceViewState(initialState));
  const setRowSelection = useCallback<OnChangeFn<RowSelectionState>>((updater) => {
    updateState((current) => ({ ...current, rowSelection: functionalUpdate(updater, current.rowSelection) }));
  }, []);
  const value = useResourceViewContextValue({ updateState, setRowSelection, resource, state });

  return (
    <ResourceViewContext.Provider value={value}>
      {children}
    </ResourceViewContext.Provider>
  );
}

function useResourceViewContextValue({
  updateState,
  setRowSelection,
  resource,
  state,
}: {
  updateState: OnChangeFn<ResourceViewState>;
  setRowSelection: OnChangeFn<RowSelectionState>;
  resource: string | undefined;
  state: ResourceViewState;
}): ResourceViewContextValue {
  const { savedFavorites, saveFavorite } = useResourceViewFavorites(resource, state);
  // Query facts belong to ResourceView; an external Router change must discard
  // old group interaction state before any newly mounted surface starts reads.
  const groupQuery = stableSerialize([resource, state.filter, state.groupStack]);
  const groupOrder = stableSerialize(state.sorting);
  const [groups, setGroups] = useState<ResourceViewGroups>(() => ({
    query: groupQuery, order: groupOrder, paginationByScope: EMPTY_GROUP_PAGINATION, expansion: null,
  }));
  const activeGroups = groupsForQuery(groups, groupQuery, groupOrder);
  const setPaginationByScope = useCallback<OnChangeFn<GroupPagination>>((updater) => {
    setGroups((current) => {
      const base = groupsForQuery(current, groupQuery, groupOrder);
      const paginationByScope = functionalUpdate(updater, base.paginationByScope);
      return base === current && paginationByScope === base.paginationByScope
        ? current : { ...base, paginationByScope };
    });
  }, [groupQuery, groupOrder]);
  const setGroupExpansion = useCallback<OnChangeFn<ResourceViewGroupExpansion | null>>((updater) => {
    setGroups((current) => {
      const base = groupsForQuery(current, groupQuery, groupOrder);
      const expansion = functionalUpdate(updater, base.expansion);
      return base === current && expansion === base.expansion
        ? current : { ...base, expansion };
    });
  }, [groupQuery, groupOrder]);
  const clearSelectedIds = useCallback(() => setRowSelection({}), [setRowSelection]);
  const resetScope = useCallback<OnChangeFn<ResourceViewState>>((updater) => {
    clearSelectedIds();
    updateState((current) => {
      const next = functionalUpdate(updater, current);
      return { ...next, rowSelection: {}, pagination: { ...next.pagination, pageIndex: 0 } };
    });
  }, [clearSelectedIds, updateState]);
  const setPagination = useCallback<OnChangeFn<PaginationState>>((updater) => {
    if (functionalUpdate(updater, state.pagination).pageSize !== state.pagination.pageSize) clearSelectedIds();
    updateState((current) => {
      const next = functionalUpdate(updater, current.pagination);
      const sizeChanged = next.pageSize !== current.pagination.pageSize;
      return {
        ...current,
        ...(sizeChanged ? { rowSelection: {} } : {}),
        pagination: {
          pageIndex: sizeChanged ? 0 : Math.max(0, Number.isFinite(next.pageIndex) ? Math.floor(next.pageIndex) : 0),
          pageSize: clampPageSize(next.pageSize),
        },
      };
    });
  }, [clearSelectedIds, state.pagination, updateState]);
  const setSorting = useCallback<OnChangeFn<SortingState>>((updater) => {
    resetScope((current) => ({ ...current, sorting: functionalUpdate(updater, current.sorting ?? []) }));
  }, [resetScope]);
  const setGroupStack = useCallback((groups: readonly ResourceViewGroup[]) => {
    const groupStack = normaliseGroupStack(groups);
    resetScope((current) => ({ ...current, group: groupStack[0] ?? null, groupStack }));
  }, [resetScope]);
  return useMemo(() => ({
    state,
    paginationByScope: activeGroups.paginationByScope,
    setPaginationByScope,
    groupExpansion: activeGroups.expansion,
    setGroupExpansion,
    savedFavorites,
    saveFavorite,
    setPagination,
    setSorting,
    setRowSelection,
    setPage: (page: number) => setPagination((current) => ({ ...current, pageIndex: page - 1 })),
    setPageSize: (pageSize: number) => setPagination((current) => ({ ...current, pageSize })),
    setFilter: (filter: ResourceViewFilter) => resetScope((current) => ({ ...current, filter })),
    setGroup: (group: ResourceViewGroup | null) => setGroupStack(group ? [group] : []),
    setGroupStack,
    toggleSelectedId: (id: string, selected?: boolean) => setRowSelection((current) => ({ ...current, [id]: selected ?? !current[id] })),
    clearSelectedIds,
    setView: (view: ResourceViewKind) => updateState((current) => ({ ...current, view })),
    setMode: (mode: CalendarViewMode) => updateState((current) => ({ ...current, mode })),
    setAnchor: (anchor: string) => updateState((current) => ({ ...current, anchor })),
    applyFavorite: (favorite: ResourceViewFavorite) => resetScope((current) => ({
      ...current,
      ...createResourceViewState({ ...favorite, sort: favorite.sort ?? null, mode: current.mode, anchor: current.anchor }),
    })),
  }), [state, activeGroups.paginationByScope, activeGroups.expansion, setPaginationByScope, setGroupExpansion, savedFavorites, saveFavorite, setPagination, setSorting, setRowSelection, resetScope, setGroupStack, clearSelectedIds, updateState]);
}

export function useResourceView(): ResourceViewContextValue {
  const value = useContext(ResourceViewContext);
  if (!value) {
    throw new Error("useResourceView must be used under ResourceViewProvider.");
  }
  return value;
}

export function useResourceViewMaybe(): ResourceViewContextValue | null {
  return useContext(ResourceViewContext);
}
