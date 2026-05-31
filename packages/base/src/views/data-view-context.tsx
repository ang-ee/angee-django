import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useQueryStates, type HistoryOptions } from "nuqs";
import type {
  ResourceTypeName,
  UseResourceListOptions,
} from "@angee/sdk";

import {
  createDataViewState,
  dataViewQueryParsers,
  dataViewReducer,
  dataViewStateFromQueryValues,
  dataViewStateToQueryValues,
  dataViewStateToResourceListOptions,
  type DataViewAction,
  type DataViewFilter,
  type DataViewGroup,
  type DataViewInitialState,
  type DataViewKind,
  type DataViewSort,
  type DataViewState,
} from "./data-view-model";

export interface DataViewContextValue {
  state: DataViewState;
  setPage: (page: number) => void;
  setPageSize: (pageSize: number) => void;
  setSort: (sort: DataViewSort | null) => void;
  setFilter: (filter: DataViewFilter) => void;
  setGroup: (group: DataViewGroup | null) => void;
  setSelectedIds: (selectedIds: Iterable<string>) => void;
  toggleSelectedId: (id: string, selected?: boolean) => void;
  clearSelectedIds: () => void;
  setView: (view: DataViewKind) => void;
  resourceListOptions: <TName extends ResourceTypeName = ResourceTypeName>(
    input: {
      fields: readonly string[];
      enabled?: boolean;
    },
  ) => UseResourceListOptions<TName>;
}

export interface DataViewProviderProps {
  children: ReactNode;
  initialState?: DataViewInitialState;
  history?: HistoryOptions;
}

const DataViewContext = createContext<DataViewContextValue | null>(null);

export function DataViewProvider({
  children,
  initialState,
  history = "push",
}: DataViewProviderProps): ReactNode {
  const [queryValues, setQueryValues] = useQueryStates(dataViewQueryParsers, {
    history,
  });
  const [selectedIds, setSelectedIdsState] = useState<ReadonlySet<string>>(
    () => new Set(initialState?.selectedIds ?? []),
  );
  const queryState = useMemo(
    () => dataViewStateFromQueryValues(queryValues, initialState),
    [queryValues, initialState],
  );
  const state = useMemo<DataViewState>(
    () => ({ ...queryState, selectedIds }),
    [queryState, selectedIds],
  );

  const dispatch = useCallback(
    (action: DataViewAction) => {
      setSelectedIdsState((current) => reduceSelectedIds(current, action));
      if (isLocalSelectionAction(action)) return;
      void setQueryValues((current) => {
        const currentState = dataViewStateFromQueryValues(current, initialState);
        const next = dataViewReducer(currentState, action);
        return dataViewStateToQueryValues(next);
      });
    },
    [initialState, setQueryValues],
  );

  const value = useMemo<DataViewContextValue>(
    () => ({
      state,
      setPage: (page) => dispatch({ type: "setPage", page }),
      setPageSize: (pageSize) =>
        dispatch({ type: "setPageSize", pageSize }),
      setSort: (sort) => dispatch({ type: "setSort", sort }),
      setFilter: (filter) => dispatch({ type: "setFilter", filter }),
      setGroup: (group) => dispatch({ type: "setGroup", group }),
      setSelectedIds: (selectedIds) =>
        dispatch({ type: "setSelectedIds", selectedIds }),
      toggleSelectedId: (id, selected) =>
        dispatch({ type: "toggleSelectedId", id, selected }),
      clearSelectedIds: () => dispatch({ type: "clearSelectedIds" }),
      setView: (view) => dispatch({ type: "setView", view }),
      resourceListOptions: (input) =>
        dataViewStateToResourceListOptions(state, input),
    }),
    [dispatch, state],
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
  return dataViewReducer(
    createDataViewState({ selectedIds }),
    action,
  ).selectedIds;
}
