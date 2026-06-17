import {
  DEFAULT_PAGE_SIZE,
  clampPageSize,
} from "@angee/sdk";

export const DATA_VIEW_KINDS = ["list", "board"] as const;
export const DATA_VIEW_GROUP_GRANULARITIES = [
  "day",
  "week",
  "month",
  "quarter",
  "year",
] as const;
export const DEFAULT_DATA_VIEW_PAGE_SIZE = DEFAULT_PAGE_SIZE;

export type DataViewKind = (typeof DATA_VIEW_KINDS)[number];
export type DataViewGroupGranularity =
  (typeof DATA_VIEW_GROUP_GRANULARITIES)[number];
export type DataViewSortDirection = "asc" | "desc";
export type DataViewOrderDirection = "ASC" | "DESC";
export const DATA_VIEW_LOOKUP_OPERATORS = [
  "exact",
  "inList",
  "isNull",
  "iExact",
  "contains",
  "iContains",
  "startsWith",
  "iStartsWith",
  "endsWith",
  "iEndsWith",
  "gt",
  "gte",
  "lt",
  "lte",
] as const;
export type DataViewLookupOperator =
  (typeof DATA_VIEW_LOOKUP_OPERATORS)[number];

/** Whether a string is one of the supported lookup operators. */
export function isLookupOperator(value: string): value is DataViewLookupOperator {
  return (DATA_VIEW_LOOKUP_OPERATORS as readonly string[]).includes(value);
}
export type DataViewFilterPrimitive = string | number | boolean | null;
export type DataViewFilterValue =
  | DataViewFilterPrimitive
  | readonly DataViewFilterValue[]
  | DataViewLookup
  | DataViewFilter;
export type DataViewLookup = {
  [operator in DataViewLookupOperator]?: DataViewFilterValue;
};
export type DataViewFilter = {
  [field: string]: DataViewFilterValue;
};
export type DataViewResourceOrder = Record<string, DataViewOrderDirection>;
// TODO: derive the text-search field from addon/schema title metadata, not a
// hardcoded product title field.
export const DEFAULT_TEXT_FILTER_FIELD = "title";

export interface DataViewSort {
  field: string;
  dir: DataViewSortDirection;
}

export interface DataViewGroup {
  field: string;
  aggregateField?: string;
  aggregateKey?: string;
  granularity?: DataViewGroupGranularity;
}

export type DataViewDefaultGroups = Partial<
  Record<DataViewKind, DataViewGroup | null>
>;

export interface DataViewInitialState {
  page?: number;
  pageSize?: number;
  sort?: DataViewSort | null;
  filter?: DataViewFilter;
  group?: DataViewGroup | null;
  groupStack?: readonly DataViewGroup[];
  selectedIds?: Iterable<string>;
  view?: DataViewKind;
}

export interface DataViewFavorite {
  id: string;
  label: string;
  pageSize?: number;
  sort?: DataViewSort | null;
  filter?: DataViewFilter;
  groupStack?: readonly DataViewGroup[];
  view?: DataViewKind;
}

export type DataViewAction =
  | { type: "setPage"; page: number }
  | { type: "setPageSize"; pageSize: number }
  | { type: "setSort"; sort: DataViewSort | null }
  | { type: "setFilter"; filter: DataViewFilter }
  | { type: "setGroup"; group: DataViewGroup | null }
  | { type: "setGroupStack"; groupStack: readonly DataViewGroup[] }
  | { type: "setSelectedIds"; selectedIds: Iterable<string> }
  | { type: "toggleSelectedId"; id: string; selected?: boolean }
  | { type: "clearSelectedIds" }
  | { type: "setView"; view: DataViewKind }
  | { type: "applyFavorite"; favorite: DataViewFavorite };

export interface FilterFacet {
  field: string;
  value: string;
}

export class Filter {
  readonly value: DataViewFilter;

  constructor(value: DataViewFilter = {}) {
    this.value = { ...value };
  }

  static from(value: DataViewFilter | undefined): Filter {
    return new Filter(value);
  }

  static facetFromFilter(filter: DataViewFilter): FilterFacet | null {
    const [entry] = Object.entries(filter);
    if (!entry) return null;
    const [field, value] = entry;
    const lookup = isDataViewLookup(value) ? value : null;
    const exact = lookup?.exact;
    return typeof exact === "string" ? { field, value: exact } : null;
  }

  hasEntries(): boolean {
    return Object.keys(this.value).length > 0;
  }

  facetValues(field: string): readonly string[] {
    const lookup = this.lookup(field);
    const exact = lookup?.exact;
    if (typeof exact === "string") return [exact];
    const inList = lookup?.inList;
    return Array.isArray(inList)
      ? inList.filter((value): value is string => typeof value === "string")
      : [];
  }

  toggleFacet(facet: FilterFacet): DataViewFilter {
    const current = this.facetValues(facet.field);
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

  withTextTerm(value: string, field = DEFAULT_TEXT_FILTER_FIELD): DataViewFilter {
    const next = { ...this.value };
    const trimmed = value.trim();
    if (trimmed) next[field] = { iContains: trimmed };
    else delete next[field];
    return next;
  }

  private lookup(field: string): DataViewLookup | null {
    const value = this.value[field];
    return isDataViewLookup(value) ? value : null;
  }
}

export class DataViewState {
  readonly page: number;
  readonly pageSize: number;
  readonly sort: DataViewSort | null;
  readonly filter: DataViewFilter;
  readonly group: DataViewGroup | null;
  readonly groupStack: readonly DataViewGroup[];
  readonly selectedIds: ReadonlySet<string>;
  readonly view: DataViewKind;

  constructor(initial: DataViewInitialState = {}) {
    const groupStack = DataViewState.normaliseGroupStack(
      initial.groupStack ?? (initial.group ? [initial.group] : []),
    );
    this.page = DataViewState.normalisePage(initial.page);
    this.pageSize = clampPageSize(
      initial.pageSize ?? DEFAULT_DATA_VIEW_PAGE_SIZE,
    );
    this.sort = initial.sort ? DataViewState.normaliseSort(initial.sort) : null;
    this.filter = DataViewState.normaliseFilter(initial.filter);
    this.group = groupStack[0] ?? null;
    this.groupStack = groupStack;
    this.selectedIds = new Set(initial.selectedIds ?? []);
    this.view = initial.view ?? "list";
  }

  static create(initial: DataViewInitialState = {}): DataViewState {
    return new DataViewState(initial);
  }

  static fromSearch(
    search: DataViewSearch | Record<string, unknown>,
    initial: DataViewInitialState = {},
  ): DataViewState {
    const base = DataViewState.create(initial);
    const page = parseSearchInteger(search.page);
    const pageSize = parseSearchInteger(search.pageSize);
    const sort = parseSearchSort(search.sort);
    const filter = parseSearchFilter(search.filter);
    const group = parseSearchGroup(search.group);
    const then = parseSearchGroupStack(search.then);
    const view = parseSearchView(search.view);
    return DataViewState.create({
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
    });
  }

  reduce(action: DataViewAction): DataViewState {
    switch (action.type) {
      case "setPage":
        return this.with({ page: DataViewState.normalisePage(action.page) });
      case "setPageSize":
        return this.resetQueryScope({
          pageSize: clampPageSize(action.pageSize),
        });
      case "setSort":
        return this.resetQueryScope({
          sort: action.sort ? DataViewState.normaliseSort(action.sort) : null,
        });
      case "setFilter":
        return this.resetQueryScope({
          filter: DataViewState.normaliseFilter(action.filter),
        });
      case "setGroup":
        return this.resetQueryScope({
          group: action.group ? DataViewState.normaliseGroup(action.group) : null,
          groupStack: action.group
            ? [DataViewState.normaliseGroup(action.group)]
            : [],
        });
      case "setGroupStack": {
        const groupStack = DataViewState.normaliseGroupStack(action.groupStack);
        return this.resetQueryScope({
          group: groupStack[0] ?? null,
          groupStack,
        });
      }
      case "setSelectedIds":
        return this.with({ selectedIds: new Set(action.selectedIds) });
      case "toggleSelectedId":
        return this.with({
          selectedIds: DataViewState.toggledSelectedIds(
            this.selectedIds,
            action,
          ),
        });
      case "clearSelectedIds":
        return this.with({ selectedIds: new Set() });
      case "setView":
        return this.with({ view: action.view });
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

  toSearch(): DataViewSearch {
    const search: DataViewSearch = {};
    if (this.page !== 1) search.page = this.page;
    if (this.pageSize !== DEFAULT_DATA_VIEW_PAGE_SIZE) {
      search.pageSize = this.pageSize;
    }
    if (this.sort) search.sort = serializeDataViewSort(this.sort);
    if (this.hasFilter()) search.filter = JSON.stringify(this.filter);
    if (this.group) search.group = serializeDataViewGroup(this.group);
    if (this.groupStack.length > 1) {
      search.then = serializeDataViewGroupStack(this.groupStack.slice(1));
    }
    if (this.view !== "list") search.view = this.view;
    return search;
  }

  hasFilter(): boolean {
    return Filter.from(this.filter).hasEntries();
  }

  resourceOrder(): DataViewResourceOrder | undefined {
    if (!this.sort) return undefined;
    return { [this.sort.field]: this.sort.dir === "asc" ? "ASC" : "DESC" };
  }

  withSelectedIds(selectedIds: Iterable<string>): DataViewState {
    return this.with({ selectedIds: new Set(selectedIds) });
  }

  toFavorite(label: string, id: string): DataViewFavorite {
    return {
      id,
      label,
      pageSize: this.pageSize,
      ...(this.sort ? { sort: this.sort } : {}),
      ...(this.hasFilter() ? { filter: this.filter } : {}),
      ...(this.groupStack.length > 0 ? { groupStack: this.groupStack } : {}),
      ...(this.view !== "list" ? { view: this.view } : {}),
    };
  }

  static normaliseGroupStack(
    groups: readonly DataViewGroup[],
  ): readonly DataViewGroup[] {
    const seen = new Set<string>();
    const normalised: DataViewGroup[] = [];
    for (const group of groups) {
      const next = DataViewState.normaliseGroup(group);
      const key = serializeDataViewGroup(next);
      if (seen.has(key)) continue;
      seen.add(key);
      normalised.push(next);
    }
    return normalised;
  }

  private with(initial: DataViewInitialState): DataViewState {
    return DataViewState.create({
      ...this.toInitialState(),
      ...initial,
    });
  }

  private resetQueryScope(initial: DataViewInitialState): DataViewState {
    return DataViewState.create({
      ...this.toInitialState(),
      ...initial,
      page: 1,
      selectedIds: [],
    });
  }

  private toInitialState(): DataViewInitialState {
    return {
      page: this.page,
      pageSize: this.pageSize,
      sort: this.sort,
      filter: this.filter,
      group: this.group,
      groupStack: this.groupStack,
      selectedIds: this.selectedIds,
      view: this.view,
    };
  }

  private static toggledSelectedIds(
    selectedIds: ReadonlySet<string>,
    action: Extract<DataViewAction, { type: "toggleSelectedId" }>,
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

  private static normaliseSort(sort: DataViewSort): DataViewSort {
    return {
      field: sort.field,
      dir: sort.dir === "desc" ? "desc" : "asc",
    };
  }

  private static normaliseGroup(group: DataViewGroup): DataViewGroup {
    return {
      field: group.field,
      ...(group.aggregateField ? { aggregateField: group.aggregateField } : {}),
      ...(group.aggregateKey ? { aggregateKey: group.aggregateKey } : {}),
      ...(group.granularity ? { granularity: group.granularity } : {}),
    };
  }

  private static normaliseFilter(
    filter: DataViewFilter | undefined,
  ): DataViewFilter {
    return Filter.from(filter).value;
  }
}

const DATA_VIEW_SEARCH_SHAPE = {
  page: undefined as number | undefined,
  pageSize: undefined as number | undefined,
  sort: undefined as string | undefined,
  filter: undefined as string | undefined,
  group: undefined as string | undefined,
  then: undefined as string | undefined,
  view: undefined as string | undefined,
};

export type DataViewSearchKey = keyof typeof DATA_VIEW_SEARCH_SHAPE;
export type DataViewSearch = Partial<typeof DATA_VIEW_SEARCH_SHAPE>;
export const DATA_VIEW_SEARCH_KEYS = Object.keys(
  DATA_VIEW_SEARCH_SHAPE,
) as DataViewSearchKey[];

export function dataViewStateToSearch(state: DataViewState): DataViewSearch {
  return state.toSearch();
}

export function dataViewSearchToState(
  search: DataViewSearch | Record<string, unknown>,
  initial: DataViewInitialState = {},
): DataViewState {
  return DataViewState.fromSearch(search, initial);
}

export function mergeDataViewSearch(
  current: Record<string, unknown>,
  next: Partial<Record<DataViewSearchKey, unknown>>,
): Record<string, unknown> {
  const merged = { ...current };
  for (const key of DATA_VIEW_SEARCH_KEYS) {
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

function parseSearchSort(value: unknown): DataViewSort | null {
  if (typeof value !== "string") return null;
  return parseDataViewSort(value);
}

function parseSearchFilter(value: unknown): DataViewFilter | null {
  if (typeof value !== "string" || value === "") return null;
  try {
    return dataViewFilterFromUnknown(JSON.parse(value));
  } catch {
    return null;
  }
}

function parseSearchGroup(value: unknown): DataViewGroup | null {
  if (typeof value !== "string") return null;
  return parseDataViewGroup(value);
}

function parseSearchGroupStack(
  value: unknown,
): readonly DataViewGroup[] | null {
  if (typeof value !== "string") return null;
  return parseDataViewGroupStack(value);
}

function parseSearchView(value: unknown): DataViewKind | null {
  if (typeof value !== "string") return null;
  return isDataViewKind(value) ? value : null;
}

function parseDataViewSort(value: string): DataViewSort | null {
  const [field, dir, extra] = value.split(":");
  if (!field || extra !== undefined) return null;
  if (dir !== "asc" && dir !== "desc") return null;
  return { field, dir };
}

function serializeDataViewSort(sort: DataViewSort): string {
  return `${sort.field}:${sort.dir}`;
}

function parseDataViewGroup(value: string): DataViewGroup | null {
  const [fieldPart, granularity, extra] = value.split(":");
  if (!fieldPart || extra !== undefined) return null;
  const group = parseDataViewGroupFields(fieldPart);
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

function parseDataViewGroupFields(value: string): Pick<
  DataViewGroup,
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

function serializeDataViewGroup(group: DataViewGroup): string {
  const field = group.aggregateField || group.aggregateKey
    ? `${group.field}~${group.aggregateField ?? group.field}~${group.aggregateKey ?? group.field}`
    : group.field;
  return group.granularity ? `${field}:${group.granularity}` : field;
}

function parseDataViewGroupStack(value: string): readonly DataViewGroup[] | null {
  if (!value) return [];
  const groups = value.split(",").map(parseDataViewGroup);
  if (groups.some((group) => group === null)) return null;
  return DataViewState.normaliseGroupStack(groups as DataViewGroup[]);
}

function serializeDataViewGroupStack(
  groups: readonly DataViewGroup[],
): string {
  return groups.map(serializeDataViewGroup).join(",");
}

export function dataViewGroupsEqual(
  left: DataViewGroup,
  right: DataViewGroup,
): boolean {
  return left.field === right.field
    && left.aggregateField === right.aggregateField
    && left.aggregateKey === right.aggregateKey
    && left.granularity === right.granularity;
}

function isGroupGranularity(value: string): value is DataViewGroupGranularity {
  return DATA_VIEW_GROUP_GRANULARITIES.includes(
    value as DataViewGroupGranularity,
  );
}

function isDataViewKind(value: string): value is DataViewKind {
  return DATA_VIEW_KINDS.includes(value as DataViewKind);
}

function dataViewFilterFromUnknown(value: unknown): DataViewFilter | null {
  if (!isDataViewFilter(value)) return null;
  return value as DataViewFilter;
}

function isDataViewLookup(value: unknown): value is DataViewLookup {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  if (Object.getPrototypeOf(value) !== Object.prototype) return false;
  const record = value as Partial<Record<DataViewLookupOperator, unknown>>;
  return DATA_VIEW_LOOKUP_OPERATORS.some((operator) =>
    Object.prototype.hasOwnProperty.call(record, operator),
  );
}

function isDataViewFilter(value: unknown): value is DataViewFilter {
  return isDataViewFilterObject(value);
}

function isDataViewFilterValue(value: unknown): boolean {
  if (value == null) return true;
  if (typeof value === "string") return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (typeof value === "boolean") return true;
  if (Array.isArray(value)) return value.every(isDataViewFilterValue);
  return isDataViewFilterObject(value);
}

function isDataViewFilterObject(value: unknown): value is DataViewFilter {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  if (Object.getPrototypeOf(value) !== Object.prototype) return false;
  return Object.values(value).every(isDataViewFilterValue);
}
