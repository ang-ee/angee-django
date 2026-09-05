import type { PaginationState, RowSelectionState, SortingState } from "@tanstack/react-table";
import { DEFAULT_CALENDAR_VIEW_MODE, DEFAULT_RESOURCE_VIEW_PAGE_SIZE, type CalendarViewMode, type ResourceViewKind } from "./capabilities";
import { Filter, type ResourceViewFilter, type ResourceViewGroup, type ResourceViewInitialState } from "./filter";
import { normaliseGroupStack, todayCalendarAnchor } from "./search";
import { normalisePageSize } from "../page-size";

/** Native table state plus the Angee view facts carried by router search. */
export interface ResourceViewState {
  pagination: PaginationState;
  /** Absent inherits a declaration; an empty native state explicitly clears it. */
  sorting?: SortingState;
  rowSelection: RowSelectionState;
  filter: ResourceViewFilter;
  group: ResourceViewGroup | null;
  groupStack: readonly ResourceViewGroup[];
  view: ResourceViewKind;
  mode: CalendarViewMode;
  anchor: string;
}

/** Decode declarative defaults at the view boundary; live state stays native. */
export function createResourceViewState(initial: ResourceViewInitialState = {}): ResourceViewState {
  const groupStack = normaliseGroupStack(initial.groupStack ?? (initial.group ? [initial.group] : []));
  return {
    pagination: {
      pageIndex: Math.max(0, Number.isFinite(initial.page) ? Math.floor(initial.page!) - 1 : 0),
      pageSize: normalisePageSize(initial.pageSize ?? DEFAULT_RESOURCE_VIEW_PAGE_SIZE),
    },
    sorting: initial.sort === undefined
      ? initial.sorting
      : initial.sort ? [{ id: initial.sort.field, desc: initial.sort.dir === "desc" }] : [],
    rowSelection: Object.fromEntries(Array.from(initial.selectedIds ?? [], (id) => [id, true])),
    filter: Filter.from(initial.filter).value,
    group: groupStack[0] ?? null,
    groupStack,
    view: initial.view ?? "list",
    mode: initial.mode ?? DEFAULT_CALENDAR_VIEW_MODE,
    anchor: initial.anchor ?? todayCalendarAnchor(),
  };
}
