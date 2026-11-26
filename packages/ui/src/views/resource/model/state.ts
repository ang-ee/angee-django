import type { PaginationState, RowSelectionState, SortingState, VisibilityState } from "@tanstack/react-table";
import { QueryParseError, type QuerySort, type ResourceQuery } from "@angee/metadata";
import { DEFAULT_CALENDAR_VIEW_MODE, DEFAULT_RESOURCE_VIEW_PAGE_SIZE, type CalendarViewMode, type ResourceViewKind } from "./capabilities";
import { Filter, type ResourceViewFilter, type ResourceViewGroup, type ResourceViewInitialState, type ResourceViewSort } from "./filter";
import { normaliseGroupStack, normaliseSourceKeys, todayCalendarAnchor } from "./search";
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
  /** Calendar source keys hidden from the grid, sorted. */
  hiddenSources: readonly string[];
}

/** Decode declarative defaults at the view boundary; live state stays native.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
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
    sorting: initial.sort === undefined ? initial.sorting : sortingFromResourceViewSort(initial.sort),
    rowSelection: Object.fromEntries(Array.from(initial.selectedIds ?? [], (id) => [id, true])),
    filter: Filter.from(initial.filter).value,
    groupStack,
    view,
    mode: initial.mode ?? DEFAULT_CALENDAR_VIEW_MODE,
    anchor: initial.anchor ?? todayCalendarAnchor(),
    hiddenSources: normaliseSourceKeys(initial.hiddenSources ?? []),
  };
}

/** A stored order as native sorting: one key, or the whole order as a list; `null` clears it. */
export function sortingFromResourceViewSort(sort: ResourceViewSort | readonly ResourceViewSort[] | null): SortingState {
  return (sort === null ? [] : "field" in sort ? [sort] : sort).map(({ field, dir }) => ({ id: field, desc: dir === "desc" }));
}

/**
 * Native sorting as a stored order: one key as its object, as every saved view has stored
 * it, and several as a list in order; `null` for none.
 */
export function resourceViewSortFromSorting(sorting: SortingState): ResourceViewSort | ResourceViewSort[] | null {
  const sorts = sorting.map(({ id, desc }): ResourceViewSort => ({ field: id, dir: desc ? "desc" : "asc" }));
  return sorts.length === 0 ? null : sorts.length === 1 ? sorts[0]! : sorts;
}

/** Native sorting as the resource query's sort keys (`ResourceQuery.sortFrom`), every key in order. */
export function querySortFromSorting(sorting: SortingState): QuerySort[] {
  return sorting.map(({ id, desc }) => ({ field: id, direction: desc ? "DESC" : "ASC" }));
}

/**
 * Validate native view query facts through their resource owner before reads.
 * `appliesRepeated` is false where the surface groups its stored stack without the
 * native server group read (in the browser, or through an authored groups
 * document): a level asking for `GroupSpec.repeated` there is refused, so the URL
 * never names a grouping the list does not apply.
 */
export function validateResourceViewState(
  state: ResourceViewState,
  query: ResourceQuery,
  { appliesRepeated = true }: { appliesRepeated?: boolean } = {},
): ResourceViewState {
  if (state.queryError) return state;
  try {
    const filter = query.filterFrom(state.filter);
    const groupStack = query.groupsFrom(state.groupStack).map((axis) => axis.spec);
    const repeated = appliesRepeated ? undefined : groupStack.find((level) => level.repeated);
    if (repeated) throw new QueryParseError(`groups.${repeated.field}.repeated`, "this list cannot list only repeated values");
    query.sortFrom(state.sorting && querySortFromSorting(state.sorting));
    return { ...state, filter, groupStack };
  } catch (error) {
    return { ...state, queryError: error instanceof Error ? error : new Error("Invalid query state.") };
  }
}
