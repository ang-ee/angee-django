import { format } from "date-fns";
import { stableSerialize } from "@angee/refine";
import { CALENDAR_ANCHOR_FORMAT, defaultResourceViewPageSize, CALENDAR_VIEW_MODES, RESOURCE_VIEW_KINDS } from "./capabilities";
import type { CalendarViewMode, ResourceViewKind } from "./capabilities";
import { Filter, isResourceViewFilter } from "./filter";
import { QueryParseError, GroupSpecsSchema } from "@angee/metadata";
import * as v from "valibot";
import type { ResourceViewFilter, ResourceViewGroup, ResourceViewInitialState, ResourceViewSort } from "./filter";
import { createResourceViewState, type ResourceViewState } from "./state";
import { normalisePageSize } from "../page-size";
const RESOURCE_VIEW_SEARCH_SHAPE = {
  page: undefined as number | undefined,
  pageSize: undefined as number | undefined,
  sort: undefined as string | undefined,
  filter: undefined as string | undefined,
  group: undefined as string | undefined,
  then: undefined as string | undefined,
  view: undefined as string | undefined,
  mode: undefined as string | undefined,
  anchor: undefined as string | undefined,
};

export type ResourceViewSearchKey = keyof typeof RESOURCE_VIEW_SEARCH_SHAPE;
export type ResourceViewSearch = Partial<typeof RESOURCE_VIEW_SEARCH_SHAPE>;
export const RESOURCE_VIEW_SEARCH_KEYS = Object.keys(
  RESOURCE_VIEW_SEARCH_SHAPE,
) as ResourceViewSearchKey[];

/** Encode only persisted view facts, retaining the existing URL vocabulary. */
export function resourceViewStateToSearch(
  state: ResourceViewState,
  initial: ResourceViewInitialState = {},
): ResourceViewSearch {
  const search: ResourceViewSearch = {};
  const base = createResourceViewState(initial);
  if (state.pagination.pageIndex !== base.pagination.pageIndex) search.page = state.pagination.pageIndex + 1;
  if (state.pagination.pageSize !== defaultResourceViewPageSize(initial)) search.pageSize = state.pagination.pageSize;
  const sort = state.sorting?.[0];
  const sortValue = sort ? `${sort.id}:${sort.desc ? "desc" : "asc"}` : "";
  if (stableSerialize(state.sorting) !== stableSerialize(base.sorting)) search.sort = sortValue;
  const filterValue = stableSerialize(state.filter);
  if (Filter.from(state.filter).hasEntries()) {
    if (filterValue !== stableSerialize(base.filter)) search.filter = JSON.stringify(state.filter);
  } else if (Filter.from(base.filter).hasEntries()) search.filter = "";
  if (state.groupStack.length > 0) {
    if (serializeResourceViewGroupStack(state.groupStack) !== serializeResourceViewGroupStack(base.groupStack)) {
      search.group = serializeResourceViewGroup(state.groupStack[0]!);
      if (state.groupStack.length > 1) search.then = serializeResourceViewGroupStack(state.groupStack.slice(1));
    }
  } else if (state.groupDefaultCleared || base.groupStack.length > 0) {
    search.group = "";
    if (base.groupStack.length > 1) search.then = "";
  }
  if (state.view !== (initial.view ?? "list")) search.view = state.view;
  if (state.view === "calendar") {
    if (state.mode !== base.mode) search.mode = state.mode;
    if (state.anchor !== base.anchor) search.anchor = state.anchor;
  }
  return search;
}

/** Decode URL syntax into native state, never a second table state model. */
export function resourceViewSearchToState(
  input: ResourceViewSearch | Record<string, unknown>,
  initial: ResourceViewInitialState = {},
  namespace?: string,
): ResourceViewState {
  const source: Record<string, unknown> = { ...input };
  const search = namespace
    ? Object.fromEntries(RESOURCE_VIEW_SEARCH_KEYS.map((key) => [key, source[resourceViewSearchKey(key, namespace)]]))
    : source;
  const base = createResourceViewState(initial);
  try {
    const sort = parseSearchSort(search.sort);
    const group = parseSearchGroup(search.group);
    const then = parseSearchGroupStack(search.then);
    const thenCleared = isClearedSearchValue(search.then);
    const groupDefaultCleared = isClearedSearchValue(search.group);
    const groupStack = groupDefaultCleared
      ? []
      : group || then || thenCleared
        ? normaliseGroupStack([...(group ? [group] : []), ...(thenCleared ? [] : (then ?? []))])
        : base.groupStack;
    const page = parseSearchInteger(search.page);
    return {
      ...base,
      pagination: {
        pageIndex: page === null ? base.pagination.pageIndex : Math.max(0, Math.floor(page) - 1),
        pageSize: normalisePageSize(parseSearchInteger(search.pageSize) ?? base.pagination.pageSize),
      },
      sorting: isClearedSearchValue(search.sort) ? [] : sort ? [{ id: sort.field, desc: sort.dir === "desc" }] : base.sorting,
      filter: isClearedSearchValue(search.filter) ? {} : parseSearchFilter(search.filter) ?? base.filter,
      group: groupStack[0] ?? null,
      groupStack,
      groupDefaultCleared,
      view: parseSearchView(search.view) ?? base.view,
      mode: parseSearchMode(search.mode) ?? base.mode,
      anchor: parseSearchAnchor(search.anchor) ?? base.anchor,
    };
  } catch (error) {
    return { ...base, queryError: error instanceof Error ? error : new QueryParseError("query", "invalid query state") };
  }
}

export function normaliseGroupStack(groups: unknown): readonly ResourceViewGroup[] {
  const parsed = v.parse(GroupSpecsSchema, groups);
  const keys = parsed.map(serializeResourceViewGroup);
  if (new Set(keys).size !== keys.length) throw new QueryParseError("groups", "duplicate group axis");
  return parsed;
}

export function mergeResourceViewSearch(
  current: Record<string, unknown>,
  next: Partial<Record<ResourceViewSearchKey, unknown>>,
  namespace?: string,
): Record<string, unknown> {
  const merged = { ...current };
  for (const key of RESOURCE_VIEW_SEARCH_KEYS) {
    const target = resourceViewSearchKey(key, namespace);
    if (Object.prototype.hasOwnProperty.call(next, key)) {
      merged[target] = next[key];
    } else {
      delete merged[target];
    }
  }
  return merged;
}

// The model emits numbers in memory; reads also accept URL-stringified values.
export function parseSearchInteger(value: unknown): number | null {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : null;
  }
  if (typeof value !== "string" || value.trim() === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function isClearedSearchValue(value: unknown): boolean {
  return value === "";
}

export function parseSearchSort(value: unknown): ResourceViewSort | null {
  if (value == null) return null;
  if (typeof value !== "string") throw new QueryParseError("sort", "expected a string");
  return parseResourceViewSort(value);
}

export function parseSearchFilter(value: unknown): ResourceViewFilter | null {
  if (value == null || value === "") return null;
  if (typeof value !== "string") throw new QueryParseError("filter", "expected JSON text");
  let parsed: unknown;
  try { parsed = JSON.parse(value); }
  catch { throw new QueryParseError("filter", "expected valid JSON"); }
  if (!isResourceViewFilter(parsed)) throw new QueryParseError("filter", "expected a filter object");
  return parsed;
}

export function parseSearchGroup(value: unknown): ResourceViewGroup | null {
  if (value == null) return null;
  if (typeof value !== "string") throw new QueryParseError("group", "expected a string");
  return parseResourceViewGroup(value);
}

export function parseSearchGroupStack(
  value: unknown,
): readonly ResourceViewGroup[] | null {
  if (value == null) return null;
  if (typeof value !== "string") throw new QueryParseError("then", "expected a string");
  return parseResourceViewGroupStack(value);
}

export function parseSearchView(value: unknown): ResourceViewKind | null {
  if (typeof value !== "string") return null;
  return isResourceViewKind(value) ? value : null;
}

export function parseSearchMode(value: unknown): CalendarViewMode | null {
  if (typeof value !== "string") return null;
  return isCalendarViewMode(value) ? value : null;
}

export function parseSearchAnchor(value: unknown): string | null {
  return typeof value === "string" && CALENDAR_ANCHOR_PATTERN.test(value)
    ? value
    : null;
}

const CALENDAR_ANCHOR_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

/** Today as a local `yyyy-MM-dd` anchor (the calendar's default reference day). */
export function todayCalendarAnchor(): string {
  return format(new Date(), CALENDAR_ANCHOR_FORMAT);
}

function parseResourceViewSort(value: string): ResourceViewSort | null {
  const [field, dir, extra] = value.split(":");
  if (!value) return null;
  if (!field || extra !== undefined || (dir !== "asc" && dir !== "desc")) throw new QueryParseError("sort", "expected field:asc or field:desc");
  return { field, dir };
}

export function serializeResourceViewSort(sort: ResourceViewSort): string {
  return `${sort.field}:${sort.dir}`;
}

function parseResourceViewGroup(value: string): ResourceViewGroup | null {
  if (!value) return null;
  const [field, granularity, extra] = value.split(":");
  if (!field || !/^[_A-Za-z][_0-9A-Za-z]*(?:\.[_A-Za-z][_0-9A-Za-z]*)*$/.test(field) || extra !== undefined || granularity === "") {
    throw new QueryParseError("group", "expected field or field:granularity");
  }
  return { field, ...(granularity ? { granularity } : {}) };
}

export function serializeResourceViewGroup(group: ResourceViewGroup): string {
  return group.granularity ? `${group.field}:${group.granularity}` : group.field;
}

function parseResourceViewGroupStack(value: string): readonly ResourceViewGroup[] | null {
  if (!value) return [];
  const groups = value.split(",").map(parseResourceViewGroup);
  if (groups.some((group) => group === null)) throw new QueryParseError("then", "expected a comma-separated group stack");
  return normaliseGroupStack(groups as ResourceViewGroup[]);
}

export function serializeResourceViewGroupStack(
  groups: readonly ResourceViewGroup[],
): string {
  return groups.map(serializeResourceViewGroup).join(",");
}

export function resourceViewGroupsEqual(
  left: ResourceViewGroup,
  right: ResourceViewGroup,
): boolean {
  return left.field === right.field
    && left.granularity === right.granularity;
}

function isResourceViewKind(value: string): value is ResourceViewKind {
  return RESOURCE_VIEW_KINDS.includes(value as ResourceViewKind);
}

function isCalendarViewMode(value: string): value is CalendarViewMode {
  return CALENDAR_VIEW_MODES.includes(value as CalendarViewMode);
}

function resourceViewSearchKey(key: ResourceViewSearchKey, namespace?: string): string {
  return namespace ? `${namespace}.${key}` : key;
}
