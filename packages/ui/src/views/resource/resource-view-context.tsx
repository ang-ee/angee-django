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
import { functionalUpdate, type OnChangeFn, type PaginationState, type RowSelectionState, type SortingState, type VisibilityState } from "@tanstack/react-table";
import { stableSerialize } from "@angee/refine";
import { Filter, ResourceQuery, useModelMetadata } from "@angee/metadata";
import { validateResourceViewState } from "./model/state";
import { normaliseGroupStack } from "./model/search";
import { normalisePageSize } from "./page-size";
import type { ResourceCollectionPresentation } from "./resource-view-types";

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
import { resourceViewPresetDefaults } from "./model/favorites";
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

interface ResourceViewGroupUpdateOptions {
  /** Initial default reconciliation preserves the restored page and selection. */
  resetScope?: boolean;
}

export interface ResourceViewContextValue {
  /** Resource whose collection state this provider owns. */
  resource?: string;
  state: ResourceViewState;
  fixedFilter?: ResourceViewFilter;
  setColumnVisibility: OnChangeFn<VisibilityState>;
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
  resetQuery: () => void;
  setGroup: (group: ResourceViewGroup | null, options?: ResourceViewGroupUpdateOptions) => void;
  setGroupStack: (groupStack: readonly ResourceViewGroup[], options?: ResourceViewGroupUpdateOptions) => void;
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
  /** Stable authored-collection identity for favorites when there is no model resource. */
  favoriteKey?: string;
  scope?: ResourceViewProviderScope;
  /** Isolate this collection's URL keys when a page contains several collections. */
  namespace?: string;
}

export type ResourceViewProviderScope = "route" | "local";

export interface ResourceViewScopeMountOptions {
  ambient: ResourceViewContextValue | null;
  resource?: string;
  scope?: "inherit" | "local";
  /** Embedded collections own local state unless the caller explicitly opts in
   * to an ambient or route-owned view. */
  presentation?: ResourceCollectionPresentation;
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
  favoriteKey,
  scope = "route",
  namespace,
}: ResourceViewProviderProps): ReactNode {
  if (scope === "local") {
    return (
      <LocalResourceViewProvider
        initialState={initialState}
        resource={resource}
        favoriteKey={favoriteKey}
      >
        {children}
      </LocalResourceViewProvider>
    );
  }
  return (
    <RouteResourceViewProvider
      initialState={initialState}
      resource={resource}
      favoriteKey={favoriteKey}
      namespace={namespace}
    >
      {children}
    </RouteResourceViewProvider>
  );
}

/** Mount under an ambient view when allowed, otherwise create the one state owner. */
export function withResourceViewScope({
  ambient,
  resource,
  scope,
  presentation,
  initialState,
  isolated = false,
  providerKey,
  children,
}: ResourceViewScopeMountOptions): ReactElement {
  const resolvedScope = scope ?? (presentation === "embedded" ? "local" : "inherit");
  if (!isolated && resolvedScope !== "local" && ambient) return children(ambient);
  return (
    <ResourceViewProvider
      key={providerKey}
      initialState={initialState}
      resource={resource}
      scope={isolated || resolvedScope === "local" ? "local" : "route"}
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
  favoriteKey,
  namespace,
}: Omit<ResourceViewProviderProps, "scope">): ReactNode {
  const search = useSearch({ strict: false });
  const { resourceViews, defaultResourceView } = useAppRuntime();
  const model = useModelMetadata(resource ?? "");
  const modelLabel = model?.resource.modelLabel ?? resource;
  const routePreset = defaultResourceView && resourceViews[defaultResourceView]?.resource === modelLabel
    ? defaultResourceView : undefined;
  const defaultsFor = useCallback((id: string | undefined) => {
    const preset = id ? resourceViews[id] : undefined;
    if (id && (!preset || preset.resource !== modelLabel)) throw new Error(`Unknown resource view "${id}" for "${modelLabel}".`);
    return resourceViewPresetDefaults(initialState, preset);
  }, [initialState, resourceViews, modelLabel]);
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
  const [rowSelection, setRowSelection] = useState<RowSelectionState>(
    () => createResourceViewState(initialState).rowSelection,
  );
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
      rowSelection,
      queryError: transitionError ?? queryState.queryError,
    }),
    [queryState, rowSelection, transitionError],
  );
  const updateState = useCallback<OnChangeFn<ResourceViewState>>(
    (updater) => {
      const next = functionalUpdate(updater, { ...queryState, rowSelection });
      if (next.queryError) {
        setFailedTransition({ search, error: next.queryError });
        return;
      }
      setFailedTransition(null);
      void navigate({
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
    [defaultsFor, readState, routePreset, navigate, namespace, queryState, rowSelection, search],
  );
  const value = useResourceViewContextValue({
    updateState,
    setRowSelection,
    resource,
    favoriteKey,
    state,
  });

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
  favoriteKey,
}: Omit<ResourceViewProviderProps, "scope">): ReactNode {
  const [state, updateState] = useState(() =>
    createResourceViewState(initialState),
  );
  const setRowSelection = useCallback<OnChangeFn<RowSelectionState>>(
    (updater) => {
      updateState((current) => ({
        ...current,
        rowSelection: functionalUpdate(updater, current.rowSelection),
      }));
    },
    [],
  );
  const value = useResourceViewContextValue({
    updateState,
    setRowSelection,
    resource,
    favoriteKey,
    state,
  });

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
  favoriteKey,
  state: sourceState,
}: {
  updateState: OnChangeFn<ResourceViewState>;
  setRowSelection: OnChangeFn<RowSelectionState>;
  resource: string | undefined;
  favoriteKey?: string;
  state: ResourceViewState;
}): ResourceViewContextValue {
  const metadata = useModelMetadata(resource ?? "");
  const { resourceViews } = useAppRuntime();
  const preset = sourceState.preset ? resourceViews[sourceState.preset] : undefined;
  const state = useMemo(() => {
    try {
      if (sourceState.preset && (!preset || preset.resource !== (metadata?.resource.modelLabel ?? resource))) {
        throw new Error(`Unknown resource view "${sourceState.preset}" for "${resource}".`);
      }
      if (!metadata) return sourceState;
      const query = ResourceQuery.from(metadata);
      query.filterFrom(preset?.fixedFilter);
      return validateResourceViewState(sourceState, query);
    } catch (error) {
      return { ...sourceState, queryError: error instanceof Error ? error : new Error("Invalid resource view.") };
    }
  }, [metadata, resource, sourceState, preset]);
  const { savedFavorites, saveFavorite } = useResourceViewFavorites(
    resource,
    state,
    favoriteKey,
  );
  // Query facts belong to ResourceView; an external Router change must discard
  // old group interaction state before any newly mounted surface starts reads.
  const groupQuery = stableSerialize([
    resource,
    state.filter,
    preset?.fixedFilter,
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
  const clearSelectedIds = useCallback(
    () => setRowSelection({}),
    [setRowSelection],
  );
  const resetScope = useCallback<OnChangeFn<ResourceViewState>>(
    (updater) => {
      clearSelectedIds();
      updateState((current) => {
        const next = functionalUpdate(updater, current);
        return {
          ...next,
          rowSelection: {},
          pagination: { ...next.pagination, pageIndex: 0 },
        };
      });
    },
    [clearSelectedIds, updateState],
  );
  const setPagination = useCallback<OnChangeFn<PaginationState>>(
    (updater) => {
      if (
        functionalUpdate(updater, state.pagination).pageSize !==
        state.pagination.pageSize
      )
        clearSelectedIds();
      updateState((current) => {
        const next = functionalUpdate(updater, current.pagination);
        const sizeChanged = next.pageSize !== current.pagination.pageSize;
        return {
          ...current,
          ...(sizeChanged ? { rowSelection: {} } : {}),
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
    [clearSelectedIds, state.pagination, updateState],
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
    (groups: readonly ResourceViewGroup[], options?: ResourceViewGroupUpdateOptions) => {
      const groupStack = normaliseGroupStack(groups);
      const update = options?.resetScope === false ? updateState : resetScope;
      update((current) => ({
        ...current,
        group: groupStack[0] ?? null,
        groupStack,
        groupDefaultCleared: groupStack.length === 0,
      }));
    },
    [resetScope, updateState],
  );
  return useMemo(
    () => ({
      resource,
      state,
      fixedFilter: preset?.fixedFilter,
      setColumnVisibility: (updater: Parameters<OnChangeFn<VisibilityState>>[0]) => updateState((current) => ({
        ...current, columnVisibility: functionalUpdate(updater, current.columnVisibility),
      })),
      paginationByScope: activeGroups.paginationByScope,
      setPaginationByScope,
      groupExpansion: activeGroups.expansion,
      setGroupExpansion,
      savedFavorites,
      saveFavorite,
      setPagination,
      setSorting,
      setRowSelection,
      setPage: (page: number) =>
        setPagination((current) => ({ ...current, pageIndex: page - 1 })),
      setPageSize: (pageSize: number) =>
        setPagination((current) => ({ ...current, pageSize })),
      setFilter: (filter: ResourceViewFilter) =>
        resetScope((current) => ({ ...current, filter, queryError: null })),
      resetQuery: () =>
        resetScope((current) => ({
          ...current,
          filter: {},
          sorting: [],
          group: null,
          groupStack: [],
          groupDefaultCleared: true,
          queryError: null,
        })),
      setGroup: (group: ResourceViewGroup | null, options?: ResourceViewGroupUpdateOptions) =>
        setGroupStack(group ? [group] : [], options),
      setGroupStack,
      toggleSelectedId: (id: string, selected?: boolean) =>
        setRowSelection((current) => ({
          ...current,
          [id]: selected ?? !current[id],
        })),
      clearSelectedIds,
      setView: (view: ResourceViewKind) =>
        updateState((current) => ({
          ...current,
          view,
          groupDefaultCleared:
            view === current.view ? current.groupDefaultCleared : false,
        })),
      setMode: (mode: CalendarViewMode) =>
        updateState((current) => ({ ...current, mode })),
      setAnchor: (anchor: string) =>
        updateState((current) => ({ ...current, anchor })),
      applyFavorite: (favorite: ResourceViewFavorite) =>
        resetScope((current) => {
          try {
            const presetId = favorite.preset ?? current.preset;
            if (presetId && resourceViews[presetId]?.resource !== (metadata?.resource.modelLabel ?? resource)) {
              throw new Error(`Unknown resource view "${presetId}" for "${resource}".`);
            }
            const favoriteState = createResourceViewState({
              ...favorite,
              preset: presetId,
              filter: Filter.from(favorite.filter).value,
              groupStack: normaliseGroupStack(favorite.groupStack ?? []),
              sort: favorite.sort ?? null,
              mode: current.mode,
              anchor: current.anchor,
            });
            return {
              ...current,
              ...favoriteState,
              groupDefaultCleared: favoriteState.groupStack.length === 0,
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
      state,
      preset,
      resourceViews,
      metadata,
      activeGroups.paginationByScope,
      activeGroups.expansion,
      setPaginationByScope,
      setGroupExpansion,
      savedFavorites,
      saveFavorite,
      setPagination,
      setSorting,
      setRowSelection,
      resetScope,
      setGroupStack,
      clearSelectedIds,
      updateState,
    ],
  );
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
