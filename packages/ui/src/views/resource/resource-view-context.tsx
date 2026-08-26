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

import {
  ResourceViewState,
  resourceViewSearchToState,
  resourceViewStateToSearch,
  mergeResourceViewSearch,
  type CalendarViewMode,
  type ResourceViewAction,
  type ResourceViewFavorite,
  type ResourceViewFilter,
  type ResourceViewGroup,
  type ResourceViewInitialState,
  type ResourceViewKind,
  type ResourceViewSort,
} from "./resource-view-model";
import { useResourceViewFavorites } from "./resource-view-favorites";

export interface ResourceViewContextValue {
  state: ResourceViewState;
  setPage: (page: number) => void;
  setPageSize: (pageSize: number) => void;
  setSort: (sort: ResourceViewSort | null) => void;
  setFilter: (filter: ResourceViewFilter) => void;
  setGroup: (group: ResourceViewGroup | null) => void;
  setGroupStack: (groupStack: readonly ResourceViewGroup[]) => void;
  setSelectedIds: (selectedIds: Iterable<string>) => void;
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
type ResourceViewActions = Omit<
  ResourceViewContextValue,
  "state" | "savedFavorites" | "saveFavorite"
>;
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
  const [selectedIds, setSelectedIdsState] = useState<ReadonlySet<string>>(
    () => new Set(initialState?.selectedIds ?? []),
  );
  const queryState = useMemo(
    () => resourceViewSearchToState(search, initialState),
    [search, initialState],
  );
  const state = useMemo<ResourceViewState>(
    () => queryState.withSelectedIds(selectedIds),
    [queryState, selectedIds],
  );

  const dispatch = useCallback(
    (action: ResourceViewAction) => {
      if (isLocalSelectionAction(action)) {
        setSelectedIdsState((current) => reduceSelectedIds(current, action));
        return;
      }
      setSelectedIdsState((current) => reduceSelectedIds(current, action));
      void navigate({
        search: (current) => {
          const currentState = resourceViewSearchToState(current, initialState);
          const next = currentState.reduce(action);
          return mergeResourceViewSearch(
            current,
            resourceViewStateToSearch(next, initialState),
          );
        },
        // View-state writes replace history so filter/sort/page churn does not
        // spam Back; selection churn stays local.
        replace: true,
      });
    },
    [initialState, navigate],
  );

  const actions = useMemo(() => createResourceViewActions(dispatch), [dispatch]);
  const value = useResourceViewContextValue({ actions, resource, state });

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
  const [state, setState] = useState(() => ResourceViewState.create(initialState));
  const dispatch = useCallback((action: ResourceViewAction) => {
    setState((current) => current.reduce(action));
  }, []);
  const actions = useMemo(() => createResourceViewActions(dispatch), [dispatch]);
  const value = useResourceViewContextValue({ actions, resource, state });

  return (
    <ResourceViewContext.Provider value={value}>
      {children}
    </ResourceViewContext.Provider>
  );
}

function useResourceViewContextValue({
  actions,
  resource,
  state,
}: {
  actions: ResourceViewActions;
  resource: string | undefined;
  state: ResourceViewState;
}): ResourceViewContextValue {
  const { savedFavorites, saveFavorite } = useResourceViewFavorites(
    resource,
    state,
  );

  const value = useMemo<ResourceViewContextValue>(
    () => ({
      state,
      savedFavorites,
      saveFavorite,
      ...actions,
    }),
    [actions, saveFavorite, savedFavorites, state],
  );
  return value;
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

function isLocalSelectionAction(
  action: ResourceViewAction,
): action is Extract<
  ResourceViewAction,
  | { type: "setSelectedIds" }
  | { type: "toggleSelectedId" }
  | { type: "clearSelectedIds" }
> {
  return (
    action.type === "setSelectedIds"
    || action.type === "toggleSelectedId"
    || action.type === "clearSelectedIds"
  );
}

function reduceSelectedIds(
  selectedIds: ReadonlySet<string>,
  action: ResourceViewAction,
): ReadonlySet<string> {
  return ResourceViewState.create({ selectedIds }).reduce(action).selectedIds;
}

function createResourceViewActions(
  dispatch: (action: ResourceViewAction) => void,
): ResourceViewActions {
  return {
    setPage: (page) => dispatch({ type: "setPage", page }),
    setPageSize: (pageSize) => dispatch({ type: "setPageSize", pageSize }),
    setSort: (sort) => dispatch({ type: "setSort", sort }),
    setFilter: (filter) => dispatch({ type: "setFilter", filter }),
    setGroup: (group) => dispatch({ type: "setGroup", group }),
    setGroupStack: (groupStack) =>
      dispatch({ type: "setGroupStack", groupStack }),
    setSelectedIds: (selectedIds) =>
      dispatch({ type: "setSelectedIds", selectedIds }),
    toggleSelectedId: (id, selected) =>
      dispatch({ type: "toggleSelectedId", id, selected }),
    clearSelectedIds: () => dispatch({ type: "clearSelectedIds" }),
    setView: (view) => dispatch({ type: "setView", view }),
    setMode: (mode) => dispatch({ type: "setMode", mode }),
    setAnchor: (anchor) => dispatch({ type: "setAnchor", anchor }),
    applyFavorite: (favorite) => dispatch({ type: "applyFavorite", favorite }),
  };
}
