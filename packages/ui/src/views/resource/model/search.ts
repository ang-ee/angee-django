import { format } from "date-fns";
import { stableSerialize } from "@angee/refine";
import { CALENDAR_ANCHOR_FORMAT, defaultResourceViewPageSize, CALENDAR_VIEW_MODES, isResourceViewKind } from "./capabilities";
import type { CalendarViewMode, ResourceViewKind } from "./capabilities";
import { Filter, isResourceViewFilter } from "./filter";
import { QueryParseError, groupAxisId, groupSpecsFrom } from "@angee/metadata";
import * as v from "valibot";
import type { ResourceViewFilter, ResourceViewGroup, ResourceViewInitialState, ResourceViewSort } from "./filter";
import { createResourceViewState, sortingFromResourceViewSort, type ResourceViewState } from "./state";
import { normalisePageSize } from "../page-size";
import { isStorageDate } from "../../../widgets/date-format";
const RESOURCE_VIEW_SEARCH_SHAPE = {
  preset: undefined as string | undefined,
  columns: undefined as string | undefined,
  page: undefined as number | undefined,
  pageSize: undefined as number | undefined,
  sort: undefined as string | undefined,
  filter: undefined as string | undefined,
  group: undefined as string | undefined,
  then: undefined as string | undefined,
  view: undefined as string | undefined,
  mode: undefined as string | undefined,
  anchor: undefined as string | undefined,
  hiddenSources: undefined as string | undefined,
};

export type ResourceViewSearchKey = keyof typeof RESOURCE_VIEW_SEARCH_SHAPE;
export type ResourceViewSearch = Partial<typeof RESOURCE_VIEW_SEARCH_SHAPE>;
/**
 * @kind null
 * @reason Codec and transport infrastructure: translates runtime data and addresses.
 */
export const RESOURCE_VIEW_SEARCH_KEYS = Object.keys(
  RESOURCE_VIEW_SEARCH_SHAPE,
) as ResourceViewSearchKey[];

/** Encode only persisted view facts, retaining the existing URL vocabulary.
 * @kind null
 * @reason Codec and transport infrastructure: translates runtime data and addresses.
 */
export function resourceViewStateToSearch(
  state: ResourceViewState,
  initial: ResourceViewInitialState = {},
): ResourceViewSearch {
  const search: ResourceViewSearch = {};
  const base = createResourceViewState({ ...initial, view: state.view });
  if (state.preset !== base.preset) search.preset = state.preset;
  if (stableSerialize(state.columnVisibility) !== stableSerialize(base.columnVisibility)) search.columns = JSON.stringify(state.columnVisibility);
  if (state.pagination.pageIndex !== base.pagination.pageIndex) search.page = state.pagination.pageIndex + 1;
  if (state.pagination.pageSize !== defaultResourceViewPageSize(initial)) search.pageSize = state.pagination.pageSize;
  if (stableSerialize(state.sorting) !== stableSerialize(base.sorting)) {
    search.sort = (state.sorting ?? []).map(({ id, desc }) => `${id}:${desc ? "desc" : "asc"}`).join(",");
  }
  const filterValue = stableSerialize(state.filter);
  if (Filter.from(state.filter).hasEntries()) {
    if (filterValue !== stableSerialize(base.filter)) search.filter = JSON.stringify(state.filter);
  } else if (Filter.from(base.filter).hasEntries()) search.filter = "";
  if (state.groupStack.length > 0) {
    if (serializeResourceViewGroupStack(state.groupStack) !== serializeResourceViewGroupStack(base.groupStack)) {
      search.group = serializeResourceViewGroup(state.groupStack[0]!);
      if (state.groupStack.length > 1) search.then = serializeResourceViewGroupStack(state.groupStack.slice(1));
    }
  } else if (base.groupStack.length > 0) {
    search.group = "";
    if (base.groupStack.length > 1) search.then = "";
  }
  if (state.view !== (initial.view ?? "list")) search.view = state.view;
  if (state.view === "calendar") {
    if (state.mode !== base.mode) search.mode = state.mode;
    if (state.anchor !== base.anchor) search.anchor = state.anchor;
    const hidden = state.hiddenSources.join(",");
    if (hidden !== base.hiddenSources.join(",")) search.hiddenSources = hidden;
  }
  return search;
}

/** Decode URL syntax into native state, never a second table state model.
 * @kind null
 * @reason Codec and transport infrastructure: translates runtime data and addresses.
 */
export function resourceViewSearchToState(
  input: ResourceViewSearch | Record<string, unknown>,
  initial: ResourceViewInitialState = {},
  namespace?: string,
): ResourceViewState {
  const source: Record<string, unknown> = { ...input };
  const search = namespace
    ? Object.fromEntries(RESOURCE_VIEW_SEARCH_KEYS.map((key) => [key, source[resourceViewSearchKey(key, namespace)]]))
    : source;
  const base = createResourceViewState({ ...initial, view: parseSearchView(search.view) ?? initial.view });
  try {
    const sort = parseSearchSort(search.sort);
    const group = parseSearchGroup(search.group);
    const then = parseSearchGroupStack(search.then);
    const thenCleared = isClearedSearchValue(search.then);
    const groupStack = isClearedSearchValue(search.group)
      ? []
      : group || then || thenCleared
        ? normaliseGroupStack([...(group ? [group] : []), ...(thenCleared ? [] : (then ?? []))])
        : base.groupStack;
    const page = parseSearchInteger(search.page);
    return {
      ...base,
      ...(typeof search.preset === "string" ? { preset: search.preset } : {}),
      columnVisibility: search.columns === undefined ? base.columnVisibility
        : v.parse(v.record(v.string(), v.boolean()), JSON.parse(String(search.columns))),
      pagination: {
        pageIndex: page === null ? base.pagination.pageIndex : Math.max(0, Math.floor(page) - 1),
        pageSize: normalisePageSize(parseSearchInteger(search.pageSize) ?? base.pagination.pageSize),
      },
      sorting: isClearedSearchValue(search.sort) ? [] : sort ? sortingFromResourceViewSort(sort) : base.sorting,
      filter: isClearedSearchValue(search.filter) ? {} : parseSearchFilter(search.filter) ?? base.filter,
      groupStack,
      mode: parseSearchMode(search.mode) ?? base.mode,
      anchor: parseSearchAnchor(search.anchor) ?? base.anchor,
      hiddenSources: isClearedSearchValue(search.hiddenSources) ? []
        : parseSearchSourceKeys(search.hiddenSources) ?? base.hiddenSources,
    };
  } catch (error) {
    return { ...base, queryError: error instanceof Error ? error : new QueryParseError("query", "invalid query state") };
  }
}

export function normaliseGroupStack(groups: unknown): readonly ResourceViewGroup[] {
  return groupSpecsFrom(groups);
}

/**
 * @kind null
 * @reason Codec and transport infrastructure: translates runtime data and addresses.
 */
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

/** `?sort=` is the whole order, a comma list of `field:asc` or `field:desc`; one key reads as it always has. */
export function parseSearchSort(value: unknown): readonly ResourceViewSort[] | null {
  if (value == null || value === "") return null;
  if (typeof value !== "string") throw new QueryParseError("sort", "expected a string");
  return value.split(",").map(parseResourceViewSort);
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
  return isStorageDate(value) ? value : null;
}

/** A calendar source key: letters, digits, `_`, `-` and `.`, so a comma list carries them. */
const CALENDAR_SOURCE_KEY_PATTERN = /^[A-Za-z0-9_.-]+$/;

/**
 * Whether a string is a valid calendar source key.
 * @kind null
 * @reason Codec and transport infrastructure: translates runtime data and addresses.
 */
export function isCalendarSourceKey(value: unknown): value is string {
  return typeof value === "string" && CALENDAR_SOURCE_KEY_PATTERN.test(value);
}

/** Valid source keys, de-duplicated and sorted, so equal sets serialize equally. */
export function normaliseSourceKeys(keys: readonly unknown[]): readonly string[] {
  return [...new Set(keys.filter(isCalendarSourceKey))].sort();
}

/** `?hiddenSources=` is a comma list of source keys; an unreadable value keeps the default. */
export function parseSearchSourceKeys(value: unknown): readonly string[] | null {
  if (typeof value !== "string" || value === "") return null;
  return normaliseSourceKeys(value.split(","));
}

/** Today as a local `yyyy-MM-dd` anchor (the calendar's default reference day).
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function todayCalendarAnchor(): string {
  return format(new Date(), CALENDAR_ANCHOR_FORMAT);
}

function parseResourceViewSort(value: string): ResourceViewSort {
  const [field, dir, extra] = value.split(":");
  if (!field || extra !== undefined || (dir !== "asc" && dir !== "desc")) {
    throw new QueryParseError("sort", "expected a comma list of field:asc or field:desc");
  }
  return { field, dir };
}

/** A level's URL spelling: `field`, `field:granularity`, either ending in `:repeated` for `GroupSpec.repeated`. */
const REPEATED_GROUP = "repeated";

function parseResourceViewGroup(value: string): ResourceViewGroup | null {
  if (!value) return null;
  const segments = value.split(":");
  const repeated = segments.length > 1 && segments.at(-1) === REPEATED_GROUP;
  const [field, granularity, extra] = repeated ? segments.slice(0, -1) : segments;
  if (!field || !/^[_A-Za-z][_0-9A-Za-z]*(?:\.[_A-Za-z][_0-9A-Za-z]*)*$/.test(field) || extra !== undefined || granularity === "") {
    throw new QueryParseError("group", "expected field or field:granularity, optionally followed by :repeated");
  }
  return { field, ...(granularity ? { granularity } : {}), ...(repeated ? { repeated: true } : {}) };
}

export function serializeResourceViewGroup(group: ResourceViewGroup): string {
  return [group.field, ...(group.granularity ? [group.granularity] : []), ...(group.repeated ? [REPEATED_GROUP] : [])].join(":");
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

/**
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function resourceViewGroupsEqual(
  left: ResourceViewGroup,
  right: ResourceViewGroup,
): boolean {
  return groupAxisId(left) === groupAxisId(right);
}

function isCalendarViewMode(value: string): value is CalendarViewMode {
  return CALENDAR_VIEW_MODES.includes(value as CalendarViewMode);
}

function resourceViewSearchKey(key: ResourceViewSearchKey, namespace?: string): string {
  return namespace ? `${namespace}.${key}` : key;
}
