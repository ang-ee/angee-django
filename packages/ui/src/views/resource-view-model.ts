import { format } from "date-fns";
import {
  ANGEE_FILTER_LOOKUP_OPERATORS,
  clampPageSize,
  type AngeeFilterLookupOperator,
} from "@angee/refine";
import { dedupeBy } from "../lib/dedupe";
import { DEFAULT_PAGE_SIZE } from "./page-size";

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
export type ResourceViewSortDirection = "asc" | "desc";
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
export type ResourceViewFilterPrimitive = string | number | boolean | null;
export type ResourceViewFilterValue =
  | ResourceViewFilterPrimitive
  | readonly ResourceViewFilterValue[]
  | ResourceViewLookup
  | ResourceViewFilter;
export type ResourceViewLookup = {
  [operator in ResourceViewFacetLookupOperator]?: ResourceViewFilterValue;
};
export type ResourceViewFilter = {
  [field: string]: ResourceViewFilterValue;
};
export type ResourceViewResourceOrder = Record<string, ResourceViewOrderDirection>;
// The fallback text-search field when a model declares no representation. The
// model-driven list resolves the real field from `recordRepresentation`
// (see `resolveTextFilterField`); this is only the default for metadata-less rows.
export const DEFAULT_TEXT_FILTER_FIELD = "title";

export interface ResourceViewSort {
  field: string;
  dir: ResourceViewSortDirection;
}

export interface ResourceViewGroup {
  field: string;
  aggregateField?: string;
  aggregateKey?: string;
  granularity?: ResourceViewGroupGranularity;
}

export type ResourceViewDefaultGroups = Partial<
  Record<ResourceViewKind, ResourceViewGroup | null>
>;

export interface ResourceViewInitialState {
  page?: number;
  pageSize?: number;
  sort?: ResourceViewSort | null;
  filter?: ResourceViewFilter;
  group?: ResourceViewGroup | null;
  groupStack?: readonly ResourceViewGroup[];
  selectedIds?: Iterable<string>;
  view?: ResourceViewKind;
  /** Calendar window mode; defaults to month. */
  mode?: CalendarViewMode;
  /** Calendar anchor day (`yyyy-MM-dd`); defaults to today. */
  anchor?: string;
}

export interface ResourceViewFavorite {
  id: string;
  label: string;
  pageSize?: number;
  sort?: ResourceViewSort | null;
  filter?: ResourceViewFilter;
  groupStack?: readonly ResourceViewGroup[];
  view?: ResourceViewKind;
}

export function resourceViewFavoritesFromJson(
  raw: string | null,
): readonly ResourceViewFavorite[] {
  try {
    const value = raw ? JSON.parse(raw) : [];
    return Array.isArray(value) ? value.filter(isResourceViewFavorite) : [];
  } catch {
    return [];
  }
}

export type ResourceViewAction =
  | { type: "setPage"; page: number }
  | { type: "setPageSize"; pageSize: number }
  | { type: "setSort"; sort: ResourceViewSort | null }
  | { type: "setFilter"; filter: ResourceViewFilter }
  | { type: "setGroup"; group: ResourceViewGroup | null }
  | { type: "setGroupStack"; groupStack: readonly ResourceViewGroup[] }
  | { type: "setSelectedIds"; selectedIds: Iterable<string> }
  | { type: "toggleSelectedId"; id: string; selected?: boolean }
  | { type: "clearSelectedIds" }
  | { type: "setView"; view: ResourceViewKind }
  | { type: "setMode"; mode: CalendarViewMode }
  | { type: "setAnchor"; anchor: string }
  | { type: "applyFavorite"; favorite: ResourceViewFavorite };

export interface FilterFacet {
  field: string;
  value: string;
  mode: "lookup" | "id";
  lookup?: ResourceViewFacetLookupOperator;
}

export class Filter {
  readonly value: ResourceViewFilter;

  constructor(value: unknown = {}) {
    const record = filterRecord(value);
    this.value = record ? ({ ...record } as ResourceViewFilter) : {};
  }

  static from(value: unknown): Filter {
    return new Filter(value);
  }

  static combine(left: unknown, right: unknown): ResourceViewFilter {
    return Filter.from(left).and(right);
  }

  static combineOptional(left: unknown, right: unknown): ResourceViewFilter | undefined {
    const filter = Filter.combine(left, right);
    return Object.keys(filter).length > 0 ? filter : undefined;
  }

  static facetFromFilter(filter: ResourceViewFilter): FilterFacet | null {
    const [entry] = Object.entries(filter);
    if (!entry) return null;
    const [field, value] = entry;
    if (typeof value === "string") return { field, value, mode: "id" };
    const lookup = isResourceViewLookup(value) ? value : null;
    if (!lookup) return null;
    for (const operator of ["sqid", "pk", "exact"] as const) {
      const lookupValue = lookup[operator];
      if (typeof lookupValue === "string") {
        return { field, value: lookupValue, mode: "lookup", lookup: operator };
      }
    }
    const [lookupValue] = Array.isArray(lookup.inList)
      ? lookup.inList.filter((item): item is string => typeof item === "string")
      : [];
    return lookupValue
      ? { field, value: lookupValue, mode: "lookup", lookup: "inList" }
      : null;
  }

  hasEntries(): boolean {
    return Object.keys(this.value).length > 0;
  }

  withoutFields(fields: Iterable<string>): ResourceViewFilter {
    const omitted = new Set(fields);
    if (omitted.size === 0) return this.value;
    return withoutFilterFields(this.value, omitted);
  }

  and(filter: unknown): ResourceViewFilter {
    const right = filterRecord(filter);
    if (!right || Object.keys(right).length === 0) return this.value;
    const next: Record<string, unknown> = { ...this.value };
    let andFilter: Record<string, unknown> | undefined;
    for (const [key, value] of Object.entries(right)) {
      if (!Object.prototype.hasOwnProperty.call(next, key)) {
        next[key] = value;
      } else if (stableSerialize(next[key]) !== stableSerialize(value)) {
        andFilter = { ...andFilter, [key]: value };
      }
    }
    if (!andFilter) return next as ResourceViewFilter;
    const existingAnd = filterRecord(next.AND);
    next.AND = existingAnd ? Filter.combine(existingAnd, andFilter) : andFilter;
    return next as ResourceViewFilter;
  }

  facetValues(facet: FilterFacet | string): readonly string[] {
    const field = typeof facet === "string" ? facet : facet.field;
    const mode = typeof facet === "string" ? "lookup" : facet.mode;
    if (mode === "id") {
      const value = this.value[field];
      return typeof value === "string" ? [value] : [];
    }
    const lookup = this.lookup(field);
    if (typeof facet !== "string" && facet.lookup && facet.lookup !== "exact") {
      const lookupValue = lookup?.[facet.lookup];
      return Array.isArray(lookupValue)
        ? lookupValue.filter((value): value is string => typeof value === "string")
        : typeof lookupValue === "string"
          ? [lookupValue]
          : [];
    }
    const exact = lookup?.exact;
    if (typeof exact === "string") return [exact];
    const inList = lookup?.inList;
    return Array.isArray(inList)
      ? inList.filter((value): value is string => typeof value === "string")
      : [];
  }

  toggleFacet(facet: FilterFacet): ResourceViewFilter {
    if (facet.mode === "id") {
      const current = this.facetValues(facet);
      const next = { ...this.value };
      if (current.includes(facet.value)) delete next[facet.field];
      else next[facet.field] = facet.value;
      return next;
    }
    if (facet.lookup && facet.lookup !== "exact") {
      const current = this.facetValues(facet);
      const next = { ...this.value };
      if (facet.lookup === "inList") {
        const nextValues = current.includes(facet.value)
          ? current.filter((value) => value !== facet.value)
          : [...current, facet.value];
        if (nextValues.length === 0) delete next[facet.field];
        else next[facet.field] = { inList: nextValues };
      } else if (current.includes(facet.value)) {
        delete next[facet.field];
      } else {
        next[facet.field] = { [facet.lookup]: facet.value };
      }
      return next;
    }
    const current = this.facetValues(facet);
    const nextValues = current.includes(facet.value)
      ? current.filter((value) => value !== facet.value)
      : [...current, facet.value];
    const next = { ...this.value };
    if (nextValues.length === 0) {
      delete next[facet.field];
    } else if (nextValues.length === 1) {
      next[facet.field] = { exact: nextValues[0] };
    } else {
      next[facet.field] = { inList: nextValues };
    }
    return next;
  }

  textTerm(field = DEFAULT_TEXT_FILTER_FIELD): string {
    const value = this.lookup(field)?.iContains;
    return typeof value === "string" ? value : "";
  }

  withTextTerm(value: string, field = DEFAULT_TEXT_FILTER_FIELD): ResourceViewFilter {
    const next = { ...this.value };
    const trimmed = value.trim();
    if (trimmed) next[field] = { iContains: trimmed };
    else delete next[field];
    return next;
  }

  private lookup(field: string): ResourceViewLookup | null {
    const value = this.value[field];
    return isResourceViewLookup(value) ? value : null;
  }
}

function withoutFilterFields(
  value: unknown,
  fields: ReadonlySet<string>,
): ResourceViewFilter {
  const record = filterRecord(value);
  if (!record) return {};
  const next: Record<string, unknown> = {};
  for (const [key, item] of Object.entries(record)) {
    if (fields.has(key)) continue;
    if (isFilterControlKey(key)) {
      const child = withoutFilterControlValue(item, fields);
      if (child !== undefined) next[key] = child;
      continue;
    }
    next[key] = item;
  }
  return next as ResourceViewFilter;
}

function withoutFilterControlValue(
  value: unknown,
  fields: ReadonlySet<string>,
): unknown {
  if (Array.isArray(value)) {
    const items = value
      .map((item) => withoutFilterFields(item, fields))
      .filter((item) => Object.keys(item).length > 0);
    return items.length > 0 ? items : undefined;
  }
  const record = filterRecord(value);
  if (!record) return value;
  const child = withoutFilterFields(record, fields);
  return Object.keys(child).length > 0 ? child : undefined;
}

function isFilterControlKey(value: string): boolean {
  return value === "AND"
    || value === "OR"
    || value === "NOT"
    || value === "and"
    || value === "or"
    || value === "not";
}

export class ResourceViewState {
  readonly page: number;
  readonly pageSize: number;
  readonly sort: ResourceViewSort | null;
  readonly filter: ResourceViewFilter;
  readonly group: ResourceViewGroup | null;
  readonly groupStack: readonly ResourceViewGroup[];
  readonly selectedIds: ReadonlySet<string>;
  readonly view: ResourceViewKind;
  readonly mode: CalendarViewMode;
  readonly anchor: string;

  constructor(initial: ResourceViewInitialState = {}) {
    const groupStack = ResourceViewState.normaliseGroupStack(
      initial.groupStack ?? (initial.group ? [initial.group] : []),
    );
    this.page = ResourceViewState.normalisePage(initial.page);
    this.pageSize = clampPageSize(
      initial.pageSize ?? DEFAULT_RESOURCE_VIEW_PAGE_SIZE,
    );
    this.sort = initial.sort ? ResourceViewState.normaliseSort(initial.sort) : null;
    this.filter = ResourceViewState.normaliseFilter(initial.filter);
    this.group = groupStack[0] ?? null;
    this.groupStack = groupStack;
    this.selectedIds = new Set(initial.selectedIds ?? []);
    this.view = initial.view ?? "list";
    this.mode = initial.mode ?? DEFAULT_CALENDAR_VIEW_MODE;
    this.anchor = initial.anchor ?? todayCalendarAnchor();
  }

  static create(initial: ResourceViewInitialState = {}): ResourceViewState {
    return new ResourceViewState(initial);
  }

  static fromSearch(
    search: ResourceViewSearch | Record<string, unknown>,
    initial: ResourceViewInitialState = {},
  ): ResourceViewState {
    const base = ResourceViewState.create(initial);
    const page = parseSearchInteger(search.page);
    const pageSize = parseSearchInteger(search.pageSize);
    const sort = parseSearchSort(search.sort);
    const filter = parseSearchFilter(search.filter);
    const group = parseSearchGroup(search.group);
    const then = parseSearchGroupStack(search.then);
    const view = parseSearchView(search.view);
    const mode = parseSearchMode(search.mode);
    const anchor = parseSearchAnchor(search.anchor);
    return ResourceViewState.create({
      ...base.toInitialState(),
      page: page ?? base.page,
      pageSize: pageSize ?? base.pageSize,
      sort: sort ?? base.sort,
      filter: filter ?? base.filter,
      group: group ?? base.group,
      groupStack:
        group || then
          ? [
              ...(group ? [group] : []),
              ...(then ?? []),
            ]
          : base.groupStack,
      view: view ?? base.view,
      mode: mode ?? base.mode,
      anchor: anchor ?? base.anchor,
    });
  }

  reduce(action: ResourceViewAction): ResourceViewState {
    switch (action.type) {
      case "setPage":
        return this.with({ page: ResourceViewState.normalisePage(action.page) });
      case "setPageSize":
        return this.resetQueryScope({
          pageSize: clampPageSize(action.pageSize),
        });
      case "setSort":
        return this.resetQueryScope({
          sort: action.sort ? ResourceViewState.normaliseSort(action.sort) : null,
        });
      case "setFilter":
        return this.resetQueryScope({
          filter: ResourceViewState.normaliseFilter(action.filter),
        });
      case "setGroup":
        return this.resetQueryScope({
          group: action.group ? ResourceViewState.normaliseGroup(action.group) : null,
          groupStack: action.group
            ? [ResourceViewState.normaliseGroup(action.group)]
            : [],
        });
      case "setGroupStack": {
        const groupStack = ResourceViewState.normaliseGroupStack(action.groupStack);
        return this.resetQueryScope({
          group: groupStack[0] ?? null,
          groupStack,
        });
      }
      case "setSelectedIds":
        return this.with({ selectedIds: new Set(action.selectedIds) });
      case "toggleSelectedId":
        return this.with({
          selectedIds: ResourceViewState.toggledSelectedIds(
            this.selectedIds,
            action,
          ),
        });
      case "clearSelectedIds":
        return this.with({ selectedIds: new Set() });
      case "setView":
        return this.with({ view: action.view });
      case "setMode":
        return this.with({ mode: action.mode });
      case "setAnchor":
        return this.with({ anchor: action.anchor });
      case "applyFavorite":
        return this.resetQueryScope({
          pageSize: action.favorite.pageSize,
          sort: action.favorite.sort ?? null,
          filter: action.favorite.filter ?? {},
          groupStack: action.favorite.groupStack ?? [],
          view: action.favorite.view ?? "list",
        });
    }
  }

  toSearch(initial: ResourceViewInitialState = {}): ResourceViewSearch {
    const search: ResourceViewSearch = {};
    const defaultPageSize = defaultResourceViewPageSize(initial);
    const defaultView = initial.view ?? "list";
    if (this.page !== 1) search.page = this.page;
    if (this.pageSize !== defaultPageSize) {
      search.pageSize = this.pageSize;
    }
    if (this.sort) search.sort = serializeResourceViewSort(this.sort);
    if (this.hasFilter()) search.filter = JSON.stringify(this.filter);
    if (this.group) search.group = serializeResourceViewGroup(this.group);
    if (this.groupStack.length > 1) {
      search.then = serializeResourceViewGroupStack(this.groupStack.slice(1));
    }
    if (this.view !== defaultView) search.view = this.view;
    // mode/anchor are calendar facts: they ride the URL only under the calendar
    // kind, so a list/board deep-link never carries them.
    if (this.view === "calendar") {
      if (this.mode !== DEFAULT_CALENDAR_VIEW_MODE) search.mode = this.mode;
      if (this.anchor !== todayCalendarAnchor()) search.anchor = this.anchor;
    }
    return search;
  }

  hasFilter(): boolean {
    return Filter.from(this.filter).hasEntries();
  }

  resourceOrder(): ResourceViewResourceOrder | undefined {
    if (!this.sort) return undefined;
    return { [this.sort.field]: this.sort.dir === "asc" ? "ASC" : "DESC" };
  }

  withSelectedIds(selectedIds: Iterable<string>): ResourceViewState {
    // Selection is the hot path (toggled on every row click). Clone by structural
    // sharing so the already-normalised sort/filter/group/groupStack KEEP their
    // references — routing through `with()`/the constructor re-normalises them into
    // new objects on a pure selection change, churning every downstream memo (and
    // every memoised row) that derives from them.
    return Object.assign(
      Object.create(ResourceViewState.prototype) as ResourceViewState,
      this,
      { selectedIds: new Set(selectedIds) },
    );
  }

  toFavorite(
    label: string,
    existingFavorites: readonly ResourceViewFavorite[] = [],
  ): ResourceViewFavorite {
    return {
      id: nextResourceViewFavoriteId(label, existingFavorites),
      label,
      pageSize: this.pageSize,
      ...(this.sort ? { sort: this.sort } : {}),
      ...(this.hasFilter() ? { filter: this.filter } : {}),
      ...(this.groupStack.length > 0 ? { groupStack: this.groupStack } : {}),
      ...(this.view !== "list" ? { view: this.view } : {}),
    };
  }

  static normaliseGroupStack(
    groups: readonly ResourceViewGroup[],
  ): readonly ResourceViewGroup[] {
    return dedupeBy(groups.map((group) => ResourceViewState.normaliseGroup(group)), serializeResourceViewGroup);
  }

  private with(initial: ResourceViewInitialState): ResourceViewState {
    return ResourceViewState.create({
      ...this.toInitialState(),
      ...initial,
    });
  }

  private resetQueryScope(initial: ResourceViewInitialState): ResourceViewState {
    return ResourceViewState.create({
      ...this.toInitialState(),
      ...initial,
      page: 1,
      selectedIds: [],
    });
  }

  private toInitialState(): ResourceViewInitialState {
    return {
      page: this.page,
      pageSize: this.pageSize,
      sort: this.sort,
      filter: this.filter,
      group: this.group,
      groupStack: this.groupStack,
      selectedIds: this.selectedIds,
      view: this.view,
      mode: this.mode,
      anchor: this.anchor,
    };
  }

  private static toggledSelectedIds(
    selectedIds: ReadonlySet<string>,
    action: Extract<ResourceViewAction, { type: "toggleSelectedId" }>,
  ): ReadonlySet<string> {
    const next = new Set(selectedIds);
    const shouldSelect = action.selected ?? !next.has(action.id);
    if (shouldSelect) next.add(action.id);
    else next.delete(action.id);
    return next;
  }

  private static normalisePage(page: number | undefined): number {
    if (page === undefined || !Number.isFinite(page)) return 1;
    return Math.max(1, Math.floor(page));
  }

  private static normaliseSort(sort: ResourceViewSort): ResourceViewSort {
    return {
      field: sort.field,
      dir: sort.dir === "desc" ? "desc" : "asc",
    };
  }

  private static normaliseGroup(group: ResourceViewGroup): ResourceViewGroup {
    return {
      field: group.field,
      ...(group.aggregateField ? { aggregateField: group.aggregateField } : {}),
      ...(group.aggregateKey ? { aggregateKey: group.aggregateKey } : {}),
      ...(group.granularity ? { granularity: group.granularity } : {}),
    };
  }

  private static normaliseFilter(
    filter: ResourceViewFilter | undefined,
  ): ResourceViewFilter {
    return Filter.from(filter).value;
  }
}

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
function parseSearchInteger(value: unknown): number | null {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : null;
  }
  if (typeof value !== "string" || value.trim() === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function parseSearchSort(value: unknown): ResourceViewSort | null {
  if (typeof value !== "string") return null;
  return parseResourceViewSort(value);
}

function parseSearchFilter(value: unknown): ResourceViewFilter | null {
  if (typeof value !== "string" || value === "") return null;
  try {
    return resourceViewFilterFromUnknown(JSON.parse(value));
  } catch {
    return null;
  }
}

function parseSearchGroup(value: unknown): ResourceViewGroup | null {
  if (typeof value !== "string") return null;
  return parseResourceViewGroup(value);
}

function parseSearchGroupStack(
  value: unknown,
): readonly ResourceViewGroup[] | null {
  if (typeof value !== "string") return null;
  return parseResourceViewGroupStack(value);
}

function parseSearchView(value: unknown): ResourceViewKind | null {
  if (typeof value !== "string") return null;
  return isResourceViewKind(value) ? value : null;
}

function parseSearchMode(value: unknown): CalendarViewMode | null {
  if (typeof value !== "string") return null;
  return isCalendarViewMode(value) ? value : null;
}

function parseSearchAnchor(value: unknown): string | null {
  return typeof value === "string" && CALENDAR_ANCHOR_PATTERN.test(value)
    ? value
    : null;
}

const CALENDAR_ANCHOR_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

/** Today as a local `yyyy-MM-dd` anchor (the calendar's default reference day). */
export function todayCalendarAnchor(): string {
  return format(new Date(), CALENDAR_ANCHOR_FORMAT);
}

function defaultResourceViewPageSize(initial: ResourceViewInitialState): number {
  return clampPageSize(initial.pageSize ?? DEFAULT_RESOURCE_VIEW_PAGE_SIZE);
}

function parseResourceViewSort(value: string): ResourceViewSort | null {
  const [field, dir, extra] = value.split(":");
  if (!field || extra !== undefined) return null;
  if (dir !== "asc" && dir !== "desc") return null;
  return { field, dir };
}

function serializeResourceViewSort(sort: ResourceViewSort): string {
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

function serializeResourceViewGroup(group: ResourceViewGroup): string {
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

function serializeResourceViewGroupStack(
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

export function stableSerialize(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(stableSerialize).join(",")}]`;
  }
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableSerialize(record[key])}`)
      .join(",")}}`;
  }
  if (value === undefined) return "undefined";
  return JSON.stringify(value);
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

function isResourceViewFavorite(value: unknown): value is ResourceViewFavorite {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const record = value as Partial<ResourceViewFavorite>;
  return typeof record.id === "string" && typeof record.label === "string";
}

function nextResourceViewFavoriteId(
  label: string,
  favorites: readonly ResourceViewFavorite[],
): string {
  const base = `favorite:${slugifyFavoriteLabel(label) || "search"}`;
  const existing = new Set(favorites.map((favorite) => favorite.id));
  if (!existing.has(base)) return base;
  for (let suffix = 2; ; suffix += 1) {
    const id = `${base}-${suffix}`;
    if (!existing.has(id)) return id;
  }
}

function slugifyFavoriteLabel(label: string): string {
  return label
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function resourceViewFilterFromUnknown(value: unknown): ResourceViewFilter | null {
  if (!isResourceViewFilter(value)) return null;
  return value as ResourceViewFilter;
}

function filterRecord(filter: unknown): Record<string, unknown> | undefined {
  if (!filter || typeof filter !== "object" || Array.isArray(filter)) {
    return undefined;
  }
  return filter as Record<string, unknown>;
}

function isResourceViewLookup(value: unknown): value is ResourceViewLookup {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  if (Object.getPrototypeOf(value) !== Object.prototype) return false;
  const record = value as Partial<Record<ResourceViewFacetLookupOperator, unknown>>;
  const operators = [
    ...RESOURCE_VIEW_LOOKUP_OPERATORS,
    ...RESOURCE_VIEW_RELATION_LOOKUP_OPERATORS,
  ];
  return operators.some((operator) =>
    Object.prototype.hasOwnProperty.call(record, operator),
  );
}

function isResourceViewFilter(value: unknown): value is ResourceViewFilter {
  return isResourceViewFilterObject(value);
}

function isResourceViewFilterValue(value: unknown): boolean {
  if (value == null) return true;
  if (typeof value === "string") return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (typeof value === "boolean") return true;
  if (Array.isArray(value)) return value.every(isResourceViewFilterValue);
  return isResourceViewFilterObject(value);
}

function isResourceViewFilterObject(value: unknown): value is ResourceViewFilter {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  if (Object.getPrototypeOf(value) !== Object.prototype) return false;
  return Object.values(value).every(isResourceViewFilterValue);
}
