import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
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
  setGroupStack: (groupStack: readonly DataViewGroup[]) => void;
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
type DataViewActions = Omit<
  DataViewContextValue,
  "state" | "resourceListOptions"
>;

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
  const mountedRef = useRef(true);
  const scheduledDispatchesRef = useRef<Set<ReturnType<typeof setTimeout>>>(
    new Set(),
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
      if (isLocalSelectionAction(action)) {
        setSelectedIdsState((current) => reduceSelectedIds(current, action));
        return;
      }
      const timeout = globalThis.setTimeout(() => {
        scheduledDispatchesRef.current.delete(timeout);
        if (!mountedRef.current) return;
        setSelectedIdsState((current) => reduceSelectedIds(current, action));
        void setQueryValues((current) => {
          const currentState = dataViewStateFromQueryValues(
            current,
            initialState,
          );
          const next = dataViewReducer(currentState, action);
          return dataViewStateToQueryValues(next);
        });
      }, 0);
      scheduledDispatchesRef.current.add(timeout);
    },
    [initialState, setQueryValues],
  );

  useEffect(
    () => {
      mountedRef.current = true;
      return () => {
        mountedRef.current = false;
        for (const timeout of scheduledDispatchesRef.current) {
          globalThis.clearTimeout(timeout);
        }
        scheduledDispatchesRef.current.clear();
      };
    },
    [],
  );

  const actions = useMemo(() => createDataViewActions(dispatch), [dispatch]);
  const resourceListOptions = useCallback(
    <TName extends ResourceTypeName = ResourceTypeName>(input: {
      fields: readonly string[];
      enabled?: boolean;
    }): UseResourceListOptions<TName> =>
      dataViewStateToResourceListOptions(state, input),
    [state],
  );

  const value = useMemo<DataViewContextValue>(
    () => ({
      state,
      ...actions,
      resourceListOptions,
    }),
    [actions, resourceListOptions, state],
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
  };
}
