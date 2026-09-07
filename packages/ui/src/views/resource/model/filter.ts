import type { SortingState } from "@tanstack/react-table";
import type { FilterOperator, FilterPrimitive, FilterValue, GroupSpec, QueryFilter } from "@angee/metadata";
import type { CalendarViewMode, ResourceViewKind, ResourceViewOrderDirection, ResourceViewSortDirection } from "./capabilities";

export { Filter, type FilterFacet, isQueryFilter as isResourceViewFilter } from "@angee/metadata";
export type ResourceViewFilterPrimitive = FilterPrimitive;
export type ResourceViewFilterValue = FilterValue;
export type ResourceViewFilter = QueryFilter;
export type ResourceViewLookup = Partial<Record<FilterOperator, FilterValue>>;
export type ResourceViewGroup = GroupSpec;
export type ResourceViewResourceOrder = Record<string, ResourceViewOrderDirection>;

/** Default free-text declaration for local rows without a model representation. */
export const DEFAULT_TEXT_FILTER_FIELD = "title";

export interface ResourceViewSort {
  field: string;
  dir: ResourceViewSortDirection;
}

export type ResourceViewDefaultGroups = Partial<Record<ResourceViewKind, GroupSpec | null>>;

export interface ResourceViewInitialState {
  page?: number;
  pageSize?: number;
  sort?: ResourceViewSort | null;
  /** Native declaration order, including secondary fields; `sort` overrides it. */
  sorting?: SortingState;
  filter?: QueryFilter;
  group?: GroupSpec | null;
  groupStack?: readonly GroupSpec[];
  selectedIds?: Iterable<string>;
  view?: ResourceViewKind;
  mode?: CalendarViewMode;
  anchor?: string;
}
