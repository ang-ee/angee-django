import type { PaginationState, RowSelectionState, SortingState, VisibilityState } from "@tanstack/react-table";
import type { ResourceQuery } from "@angee/metadata";
import { DEFAULT_CALENDAR_VIEW_MODE, DEFAULT_RESOURCE_VIEW_PAGE_SIZE, type CalendarViewMode, type ResourceViewKind } from "./capabilities";
import { Filter, type ResourceViewFilter, type ResourceViewGroup, type ResourceViewInitialState } from "./filter";
import { normaliseGroupStack, todayCalendarAnchor } from "./search";
import { normalisePageSize } from "../page-size";

/** Native table state plus the Angee view facts carried by router search. */
export interface ResourceViewState {
  preset?: string;
  columnVisibility: VisibilityState;
  pagination: PaginationState;
  /** Absent inherits a declaration; an empty native state explicitly clears it. */
  sorting?: SortingState;
  rowSelection: RowSelectionState;
  filter: ResourceViewFilter;
  /** Failed boundary validation prevents dependent reads until state is repaired. */
  queryError?: Error | null;
  groupStack: readonly ResourceViewGroup[];
  view: ResourceViewKind;
  mode: CalendarViewMode;
  anchor: string;
}

/** Decode declarative defaults at the view boundary; live state stays native. */
export function createResourceViewState(initial: ResourceViewInitialState = {}): ResourceViewState {
  const view = initial.view ?? "list";
  const groupStack = normaliseGroupStack(initial.groupStacks?.[view] === null ? []
    : initial.groupStacks?.[view] ?? initial.groupStack ?? (initial.group ? [initial.group] : []));
  return {
    ...(initial.preset ? { preset: initial.preset } : {}),
    columnVisibility: initial.columnVisibility ?? {},
    pagination: {
      pageIndex: Math.max(0, Number.isFinite(initial.page) ? Math.floor(initial.page!) - 1 : 0),
      pageSize: normalisePageSize(initial.pageSize ?? DEFAULT_RESOURCE_VIEW_PAGE_SIZE),
    },
    sorting: initial.sort === undefined
      ? initial.sorting
      : initial.sort ? [{ id: initial.sort.field, desc: initial.sort.dir === "desc" }] : [],
    rowSelection: Object.fromEntries(Array.from(initial.selectedIds ?? [], (id) => [id, true])),
    filter: Filter.from(initial.filter).value,
    groupStack,
    view,
    mode: initial.mode ?? DEFAULT_CALENDAR_VIEW_MODE,
    anchor: initial.anchor ?? todayCalendarAnchor(),
  };
}

/** Validate native view query facts through their resource owner before reads. */
export function validateResourceViewState(state: ResourceViewState, query: ResourceQuery): ResourceViewState {
  if (state.queryError) return state;
  try {
    const filter = query.filterFrom(state.filter);
    const groupStack = query.groupsFrom(state.groupStack).map((axis) => axis.spec);
    query.sortFrom(state.sorting?.map(({ id, desc }) => ({ field: id, direction: desc ? "DESC" : "ASC" })));
    return { ...state, filter, groupStack };
  } catch (error) {
    return { ...state, queryError: error instanceof Error ? error : new Error("Invalid query state.") };
  }
}
