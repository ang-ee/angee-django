import { format } from "date-fns";
import { CALENDAR_ANCHOR_FORMAT, CALENDAR_VIEW_MODES, RESOURCE_VIEW_GROUP_GRANULARITIES, RESOURCE_VIEW_KINDS } from "./capabilities";
import type { CalendarViewMode, ResourceViewGroupGranularity, ResourceViewKind } from "./capabilities";
import { resourceViewFilterFromUnknown } from "./filter";
import type { ResourceViewFilter, ResourceViewGroup, ResourceViewInitialState, ResourceViewSort } from "./filter";
import { ResourceViewState } from "./state";
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

export function resourceViewStateToSearch(
  state: ResourceViewState,
  initial: ResourceViewInitialState = {},
): ResourceViewSearch {
  return state.toSearch(initial);
}

export function resourceViewSearchToState(
  search: ResourceViewSearch | Record<string, unknown>,
  initial: ResourceViewInitialState = {},
): ResourceViewState {
  return ResourceViewState.fromSearch(search, initial);
}

export function mergeResourceViewSearch(
  current: Record<string, unknown>,
  next: Partial<Record<ResourceViewSearchKey, unknown>>,
): Record<string, unknown> {
  const merged = { ...current };
  for (const key of RESOURCE_VIEW_SEARCH_KEYS) {
    if (Object.prototype.hasOwnProperty.call(next, key)) {
      merged[key] = next[key];
    } else {
      delete merged[key];
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
  if (typeof value !== "string") return null;
  return parseResourceViewSort(value);
}

export function parseSearchFilter(value: unknown): ResourceViewFilter | null {
  if (typeof value !== "string" || value === "") return null;
  try {
    return resourceViewFilterFromUnknown(JSON.parse(value));
  } catch {
    return null;
  }
}

export function parseSearchGroup(value: unknown): ResourceViewGroup | null {
  if (typeof value !== "string") return null;
  return parseResourceViewGroup(value);
}

export function parseSearchGroupStack(
  value: unknown,
): readonly ResourceViewGroup[] | null {
  if (typeof value !== "string") return null;
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
  if (!field || extra !== undefined) return null;
  if (dir !== "asc" && dir !== "desc") return null;
  return { field, dir };
}

export function serializeResourceViewSort(sort: ResourceViewSort): string {
  return `${sort.field}:${sort.dir}`;
}

function parseResourceViewGroup(value: string): ResourceViewGroup | null {
  const [fieldPart, granularity, extra] = value.split(":");
  if (!fieldPart || extra !== undefined) return null;
  const group = parseResourceViewGroupFields(fieldPart);
  if (!group) return null;
  const { field, aggregateField, aggregateKey } = group;
  if (granularity === undefined || granularity === "") {
    return {
      field,
      ...(aggregateField ? { aggregateField } : {}),
      ...(aggregateKey ? { aggregateKey } : {}),
    };
  }
  if (!isGroupGranularity(granularity)) return null;
  return {
    field,
    ...(aggregateField ? { aggregateField } : {}),
    ...(aggregateKey ? { aggregateKey } : {}),
    granularity,
  };
}

function parseResourceViewGroupFields(value: string): Pick<
  ResourceViewGroup,
  "field" | "aggregateField" | "aggregateKey"
> | null {
  const parts = value.split("~");
  if (parts.length === 1) return parts[0] ? { field: parts[0] } : null;
  const [field, aggregateField, aggregateKey, extra] = parts;
  if (!field || !aggregateField || !aggregateKey || extra !== undefined) {
    return null;
  }
  return { field, aggregateField, aggregateKey };
}

export function serializeResourceViewGroup(group: ResourceViewGroup): string {
  const field = group.aggregateField || group.aggregateKey
    ? `${group.field}~${group.aggregateField ?? group.field}~${group.aggregateKey ?? group.field}`
    : group.field;
  return group.granularity ? `${field}:${group.granularity}` : field;
}

function parseResourceViewGroupStack(value: string): readonly ResourceViewGroup[] | null {
  if (!value) return [];
  const groups = value.split(",").map(parseResourceViewGroup);
  if (groups.some((group) => group === null)) return null;
  return ResourceViewState.normaliseGroupStack(groups as ResourceViewGroup[]);
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
    && left.aggregateField === right.aggregateField
    && left.aggregateKey === right.aggregateKey
    && left.granularity === right.granularity;
}

function isGroupGranularity(value: string): value is ResourceViewGroupGranularity {
  return RESOURCE_VIEW_GROUP_GRANULARITIES.includes(
    value as ResourceViewGroupGranularity,
  );
}

function isResourceViewKind(value: string): value is ResourceViewKind {
  return RESOURCE_VIEW_KINDS.includes(value as ResourceViewKind);
}

function isCalendarViewMode(value: string): value is CalendarViewMode {
  return CALENDAR_VIEW_MODES.includes(value as CalendarViewMode);
}
