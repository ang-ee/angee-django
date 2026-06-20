import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";

import {
  DataViewState,
  dataViewFavoritesFromJson,
  dataViewSearchToState,
  dataViewStateToSearch,
  mergeDataViewSearch,
  type DataViewAction,
  type DataViewFavorite,
  type DataViewFilter,
  type DataViewGroup,
  type DataViewInitialState,
  type DataViewKind,
  type DataViewSort,
} from "./data-view-model";

export interface DataViewContextValue {
  state: DataViewState;
  setPage: (page: number) => void;
  setPageSize: (pageSize: number) => void;
  setSort: (sort: DataViewSort | null) => void;
  setFilter: (filter: DataViewFilter) => void;
  setGroup: (group: DataViewGroup | null) => void;
  setGroupStack: (groupStack: readonly DataViewGroup[]) => void;
  setSelectedIds: (selectedIds: Iterable<string>) => void;
  toggleSelectedId: (id: string, selected?: boolean) => void;
  clearSelectedIds: () => void;
  setView: (view: DataViewKind) => void;
  savedFavorites: readonly DataViewFavorite[];
  saveFavorite: (label: string) => void;
  applyFavorite: (favorite: DataViewFavorite) => void;
}

export interface DataViewProviderProps {
  children: ReactNode;
  initialState?: DataViewInitialState;
  resource?: string;
}

const DataViewContext = createContext<DataViewContextValue | null>(null);
type DataViewActions = Omit<
  DataViewContextValue,
  "state" | "savedFavorites" | "saveFavorite"
>;
type DataViewNavigate = (options: {
  search: (current: Record<string, unknown>) => Record<string, unknown>;
  replace?: boolean;
}) => Promise<void> | void;

export function DataViewProvider({
  children,
  initialState,
  resource,
}: DataViewProviderProps): ReactNode {
  const search = useSearch({ strict: false });
  const favoriteStorageKey = resource
    ? `angee:data-view:${resource}:favorites`
    : null;
  // Narrow Router navigation to functional search updates; no from is supplied
  // because the updater is route-agnostic.
  const navigate = useNavigate() as DataViewNavigate;
  const [selectedIds, setSelectedIdsState] = useState<ReadonlySet<string>>(
    () => new Set(initialState?.selectedIds ?? []),
  );
  const [savedFavorites, setSavedFavorites] = useState<
    readonly DataViewFavorite[]
  >(() => readFavorites(favoriteStorageKey));
  const queryState = useMemo(
    () => dataViewSearchToState(search, initialState),
    [search, initialState],
  );
  const state = useMemo<DataViewState>(
    () => queryState.withSelectedIds(selectedIds),
    [queryState, selectedIds],
  );

  const dispatch = useCallback(
    (action: DataViewAction) => {
      if (isLocalSelectionAction(action)) {
        setSelectedIdsState((current) => reduceSelectedIds(current, action));
        return;
      }
      setSelectedIdsState((current) => reduceSelectedIds(current, action));
      void navigate({
        search: (current) => {
          const currentState = dataViewSearchToState(current, initialState);
          const next = currentState.reduce(action);
          return mergeDataViewSearch(current, dataViewStateToSearch(next));
        },
        // View-state writes replace history so filter/sort/page churn does not
        // spam Back; selection churn stays local.
        replace: true,
      });
    },
    [initialState, navigate],
  );

  const actions = useMemo(() => createDataViewActions(dispatch), [dispatch]);

  useEffect(() => {
    writeFavorites(favoriteStorageKey, savedFavorites);
  }, [favoriteStorageKey, savedFavorites]);

  const saveFavorite = useCallback(
    (label: string) => {
      const trimmed = label.trim();
      if (!trimmed) return;
      setSavedFavorites((current) => [
        ...current,
        state.toFavorite(trimmed, current),
      ]);
    },
    [state],
  );

  const value = useMemo<DataViewContextValue>(
    () => ({
      state,
      savedFavorites,
      saveFavorite,
      ...actions,
    }),
    [actions, saveFavorite, savedFavorites, state],
  );

  return (
    <DataViewContext.Provider value={value}>
      {children}
    </DataViewContext.Provider>
  );
}

export function useDataView(): DataViewContextValue {
  const value = useContext(DataViewContext);
  if (!value) {
    throw new Error("useDataView must be used under DataViewProvider.");
  }
  return value;
}

export function useDataViewMaybe(): DataViewContextValue | null {
  return useContext(DataViewContext);
}

function isLocalSelectionAction(
  action: DataViewAction,
): action is Extract<
  DataViewAction,
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
  action: DataViewAction,
): ReadonlySet<string> {
  return DataViewState.create({ selectedIds }).reduce(action).selectedIds;
}

function createDataViewActions(
  dispatch: (action: DataViewAction) => void,
): DataViewActions {
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
    applyFavorite: (favorite) => dispatch({ type: "applyFavorite", favorite }),
  };
}

function readFavorites(
  storageKey: string | null,
): readonly DataViewFavorite[] {
  const storage = favoriteStorage();
  if (!storageKey || !storage) return [];
  try {
    return dataViewFavoritesFromJson(storage.getItem(storageKey));
  } catch {
    return [];
  }
}

function writeFavorites(
  storageKey: string | null,
  favorites: readonly DataViewFavorite[],
): void {
  const storage = favoriteStorage();
  if (!storageKey || !storage) return;
  storage.setItem(storageKey, JSON.stringify(favorites));
}

function favoriteStorage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage ?? null;
  } catch {
    return null;
  }
}
