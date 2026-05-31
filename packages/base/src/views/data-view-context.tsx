import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  type ReactNode,
} from "react";
import { useQueryStates, type HistoryOptions } from "nuqs";
import type {
  ResourceTypeName,
  UseResourceListOptions,
} from "@angee/sdk";

import {
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
  setSelection: (selection: Iterable<string>) => void;
  toggleSelection: (id: string, selected?: boolean) => void;
  clearSelection: () => void;
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
  const state = useMemo(
    () => dataViewStateFromQueryValues(queryValues, initialState),
    [queryValues, initialState],
  );

  const dispatch = useCallback(
    (action: DataViewAction) => {
      const next = dataViewReducer(state, action);
      void setQueryValues(dataViewStateToQueryValues(next));
    },
    [setQueryValues, state],
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
      setSelection: (selection) =>
        dispatch({ type: "setSelection", selection }),
      toggleSelection: (id, selected) =>
        dispatch({ type: "toggleSelection", id, selected }),
      clearSelection: () => dispatch({ type: "clearSelection" }),
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
