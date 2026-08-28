import { ANGEE_FILTER_LOOKUP_OPERATORS, clampPageSize, type AngeeFilterLookupOperator } from "@angee/refine";
import { DEFAULT_PAGE_SIZE } from "../page-size";
import type { ResourceViewInitialState } from "./filter";
export const RESOURCE_VIEW_KINDS = ["list", "board", "calendar"] as const;

/** The calendar kind's window modes; `month` is the default period. */
export const CALENDAR_VIEW_MODES = ["month", "week", "day"] as const;
export type CalendarViewMode = (typeof CALENDAR_VIEW_MODES)[number];
export const DEFAULT_CALENDAR_VIEW_MODE: CalendarViewMode = "month";
/** The anchor date's serialized shape — a local `yyyy-MM-dd`. */
export const CALENDAR_ANCHOR_FORMAT = "yyyy-MM-dd";
export const RESOURCE_VIEW_GROUP_GRANULARITIES = [
  "day",
  "week",
  "month",
  "quarter",
  "year",
] as const;
export const DEFAULT_RESOURCE_VIEW_PAGE_SIZE = DEFAULT_PAGE_SIZE;

export type ResourceViewKind = (typeof RESOURCE_VIEW_KINDS)[number];
export type ResourceListOrder = Record<string, unknown>;

/**
 * Which data-controls a kind can carry — the owner map on the kind. The toolbar
 * reads the active kind's applicability to gate the filter/search box, the pager,
 * the group-by picker, and the columns chooser rather than each page hiding them.
 * `requiresSources` marks a kind offered only where the composing page declares
 * the data it needs (the calendar's windowed occurrence sources).
 */
export interface ResourceViewKindCapabilities {
  /** The group-by picker + group/board lane renderers apply. */
  grouping: boolean;
  /** The pager applies. */
  pagination: boolean;
  /** The column show/hide chooser applies. */
  columns: boolean;
  /** The filter/search box applies. */
  filter: boolean;
  /** The kind is offered only where the page declares its data source. */
  requiresSources?: boolean;
}

/** The applicable data-controls per resource-view kind (the owner map on the kind). */
export const RESOURCE_VIEW_KIND_CAPABILITIES: Record<
  ResourceViewKind,
  ResourceViewKindCapabilities
> = {
  list: { grouping: true, pagination: true, columns: true, filter: true },
  // Board grouping may be backed by a declared laneSource; the kind still uses
  // the group control, but lanes can come from the relation owner instead of rows.
  board: { grouping: true, pagination: true, columns: false, filter: true },
  // A windowed occurrence fetch takes only window args in v1: no pagination, no
  // group-by, no columns chooser, and no filter/search (a filterable calendar is
  // a named follow-up needing backend query args).
  calendar: {
    grouping: false,
    pagination: false,
    columns: false,
    filter: false,
    requiresSources: true,
  },
};

/** All applicable, for a surface (e.g. an in-memory rows list) that names no kind. */
export const FULL_RESOURCE_VIEW_KIND_CAPABILITIES: ResourceViewKindCapabilities = {
  grouping: true,
  pagination: true,
  columns: true,
  filter: true,
};

/** The active kind's applicability, or all-applicable when no kind is named. */
export function resourceViewKindCapabilities(
  view: ResourceViewKind | undefined,
): ResourceViewKindCapabilities {
  return view
    ? RESOURCE_VIEW_KIND_CAPABILITIES[view]
    : FULL_RESOURCE_VIEW_KIND_CAPABILITIES;
}

/**
 * The kinds a page offers, in declaration order: every kind whose capability is
 * unconditional, plus each `requiresSources` kind whose data the page declares.
 * The switcher's options derive from this — never a hardcoded array.
 */
export function availableResourceViewKinds(
  declared: { calendar?: boolean } = {},
): readonly ResourceViewKind[] {
  return RESOURCE_VIEW_KINDS.filter((kind) => {
    if (!RESOURCE_VIEW_KIND_CAPABILITIES[kind].requiresSources) return true;
    if (kind === "calendar") return declared.calendar ?? false;
    return false;
  });
}

export type ResourceViewGroupGranularity =
  (typeof RESOURCE_VIEW_GROUP_GRANULARITIES)[number];
export const RESOURCE_VIEW_SORT_DIRECTIONS = ["asc", "desc"] as const;
export type ResourceViewSortDirection =
  (typeof RESOURCE_VIEW_SORT_DIRECTIONS)[number];
export type ResourceViewOrderDirection = "ASC" | "DESC";
export const RESOURCE_VIEW_LOOKUP_OPERATORS = ANGEE_FILTER_LOOKUP_OPERATORS;
export type ResourceViewLookupOperator = AngeeFilterLookupOperator;
export const RESOURCE_VIEW_RELATION_LOOKUP_OPERATORS = ["sqid", "pk"] as const;
export type ResourceViewRelationLookupOperator =
  (typeof RESOURCE_VIEW_RELATION_LOOKUP_OPERATORS)[number];
export type ResourceViewFacetLookupOperator =
  | ResourceViewLookupOperator
  | ResourceViewRelationLookupOperator;

/** Whether a string is one of the supported lookup operators. */
export function isLookupOperator(value: string): value is ResourceViewLookupOperator {
  return (RESOURCE_VIEW_LOOKUP_OPERATORS as readonly string[]).includes(value);
}

export function defaultResourceViewPageSize(initial: ResourceViewInitialState): number {
  return clampPageSize(initial.pageSize ?? DEFAULT_RESOURCE_VIEW_PAGE_SIZE);
}
