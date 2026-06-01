import {
  createLoader,
  createParser,
  createSerializer,
  parseAsInteger,
  parseAsJson,
  parseAsStringLiteral,
  type LoaderInput,
  type inferParserType,
} from "nuqs";
import {
  DEFAULT_PAGE_SIZE,
  clampPageSize,
  type ResourceTypeName,
  type UseResourceListOptions,
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
export type DataViewFilter = Record<string, unknown>;
export type DataViewResourceOrder = Record<string, DataViewOrderDirection>;

export interface DataViewSort {
  field: string;
  dir: DataViewSortDirection;
}

export interface DataViewGroup {
  field: string;
  granularity?: DataViewGroupGranularity;
}

export interface DataViewState {
  page: number;
  pageSize: number;
  sort: DataViewSort | null;
  filter: DataViewFilter;
  group: DataViewGroup | null;
  groupStack: readonly DataViewGroup[];
  selectedIds: ReadonlySet<string>;
  view: DataViewKind;
}

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
  | { type: "setView"; view: DataViewKind };

export const dataViewSortParser = createParser<DataViewSort>({
  parse: parseDataViewSort,
  serialize: serializeDataViewSort,
  eq: dataViewSortsEqual,
});

export const dataViewGroupParser = createParser<DataViewGroup>({
  parse: parseDataViewGroup,
  serialize: serializeDataViewGroup,
  eq: dataViewGroupsEqual,
});

export const dataViewGroupStackParser = createParser<readonly DataViewGroup[]>({
  parse: parseDataViewGroupStack,
  serialize: serializeDataViewGroupStack,
  eq: dataViewGroupStacksEqual,
});

export const dataViewFilterParser =
  parseAsJson<DataViewFilter>(dataViewFilterFromUnknown);

export const dataViewQueryParsers = {
  page: parseAsInteger.withDefault(1),
  pageSize: parseAsInteger.withDefault(DEFAULT_DATA_VIEW_PAGE_SIZE),
  sort: dataViewSortParser,
  filter: dataViewFilterParser,
  group: dataViewGroupParser,
  then: dataViewGroupStackParser,
  view: parseAsStringLiteral(DATA_VIEW_KINDS).withDefault("list"),
};

export type DataViewQueryValues = inferParserType<typeof dataViewQueryParsers>;

const loadDataViewQuery = createLoader(dataViewQueryParsers);
const serializeDataViewQuery = createSerializer(dataViewQueryParsers);

export function createDataViewState(
  initial: DataViewInitialState = {},
): DataViewState {
  const groupStack = normaliseGroupStack(
    initial.groupStack ?? (initial.group ? [initial.group] : []),
  );
  return {
    page: normalisePage(initial.page),
    pageSize: clampPageSize(
      initial.pageSize ?? DEFAULT_DATA_VIEW_PAGE_SIZE,
    ),
    sort: initial.sort ? normaliseSort(initial.sort) : null,
    filter: normaliseFilter(initial.filter),
    group: groupStack[0] ?? null,
    groupStack,
    selectedIds: new Set(initial.selectedIds ?? []),
    view: initial.view ?? "list",
  };
}

export function dataViewReducer(
  state: DataViewState,
  action: DataViewAction,
): DataViewState {
  switch (action.type) {
    case "setPage":
      return { ...state, page: normalisePage(action.page) };
    case "setPageSize":
      return resetQueryScope({
        ...state,
        pageSize: clampPageSize(action.pageSize),
      });
    case "setSort":
      return resetQueryScope({
        ...state,
        sort: action.sort ? normaliseSort(action.sort) : null,
      });
    case "setFilter":
      return resetQueryScope({
        ...state,
        filter: normaliseFilter(action.filter),
      });
    case "setGroup":
      return resetQueryScope({
        ...state,
        group: action.group ? normaliseGroup(action.group) : null,
        groupStack: action.group ? [normaliseGroup(action.group)] : [],
      });
    case "setGroupStack": {
      const groupStack = normaliseGroupStack(action.groupStack);
      return resetQueryScope({
        ...state,
        group: groupStack[0] ?? null,
        groupStack,
      });
    }
    case "setSelectedIds":
      return { ...state, selectedIds: new Set(action.selectedIds) };
    case "toggleSelectedId":
      return { ...state, selectedIds: toggledSelectedIds(state.selectedIds, action) };
    case "clearSelectedIds":
      return { ...state, selectedIds: new Set() };
    case "setView":
      return { ...state, view: action.view };
  }
}

export function dataViewStateToQueryValues(
  state: DataViewState,
): Partial<DataViewQueryValues> {
  return {
    page: state.page,
    pageSize: state.pageSize,
    sort: state.sort,
    filter: hasFilter(state.filter) ? state.filter : null,
    group: state.group,
    then: state.groupStack.length > 1 ? state.groupStack.slice(1) : null,
    view: state.view,
  };
}

export function dataViewStateFromQueryValues(
  values: DataViewQueryValues,
  initial: DataViewInitialState = {},
): DataViewState {
  const base = createDataViewState(initial);
  return createDataViewState({
    page: values.page ?? base.page,
    pageSize: values.pageSize ?? base.pageSize,
    sort: values.sort ?? base.sort,
    filter: values.filter ?? base.filter,
    group: values.group ?? base.group,
    groupStack:
      values.group || values.then
        ? [
            ...(values.group ? [values.group] : []),
            ...(values.then ?? []),
          ]
        : base.groupStack,
    view: values.view ?? base.view,
  });
}

export function parseDataViewSearchParams(
  input: LoaderInput,
  initial: DataViewInitialState = {},
): DataViewState {
  return dataViewStateFromQueryValues(loadDataViewQuery(input), initial);
}

export function serializeDataViewState(state: DataViewState): string {
  return serializeDataViewQuery(dataViewStateToQueryValues(state));
}

export function dataViewSortToResourceOrder(
  sort: DataViewSort | null,
): DataViewResourceOrder | undefined {
  if (!sort) return undefined;
  return { [sort.field]: sort.dir === "asc" ? "ASC" : "DESC" };
}

export function dataViewStateToResourceListOptions<
  TName extends ResourceTypeName = ResourceTypeName,
>(
  state: DataViewState,
  input: {
    fields: readonly string[];
    enabled?: boolean;
  },
): UseResourceListOptions<TName> {
  const options: UseResourceListOptions<TName> = {
    fields: input.fields,
    pageSize: state.pageSize,
    page: state.page,
  };
  if (hasFilter(state.filter)) options.filter = state.filter;
  const order = dataViewSortToResourceOrder(state.sort);
  if (order) options.order = order;
  if (input.enabled !== undefined) options.enabled = input.enabled;
  return options;
}

function resetQueryScope(state: DataViewState): DataViewState {
  return { ...state, page: 1, selectedIds: new Set() };
}

function toggledSelectedIds(
  selectedIds: ReadonlySet<string>,
  action: Extract<DataViewAction, { type: "toggleSelectedId" }>,
): ReadonlySet<string> {
  const next = new Set(selectedIds);
  const shouldSelect = action.selected ?? !next.has(action.id);
  if (shouldSelect) next.add(action.id);
  else next.delete(action.id);
  return next;
}

function normalisePage(page: number | undefined): number {
  if (page === undefined || !Number.isFinite(page)) return 1;
  return Math.max(1, Math.floor(page));
}

function normaliseSort(sort: DataViewSort): DataViewSort {
  return {
    field: sort.field,
    dir: sort.dir === "desc" ? "desc" : "asc",
  };
}

function normaliseGroup(group: DataViewGroup): DataViewGroup {
  return {
    field: group.field,
    ...(group.granularity ? { granularity: group.granularity } : {}),
  };
}

function normaliseGroupStack(
  groups: readonly DataViewGroup[],
): readonly DataViewGroup[] {
  const seen = new Set<string>();
  const normalised: DataViewGroup[] = [];
  for (const group of groups) {
    const next = normaliseGroup(group);
    const key = serializeDataViewGroup(next);
    if (seen.has(key)) continue;
    seen.add(key);
    normalised.push(next);
  }
  return normalised;
}

function normaliseFilter(filter: DataViewFilter | undefined): DataViewFilter {
  return filter ? { ...filter } : {};
}

function hasFilter(filter: DataViewFilter): boolean {
  return Object.keys(filter).length > 0;
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

function dataViewSortsEqual(
  left: DataViewSort,
  right: DataViewSort,
): boolean {
  return left.field === right.field && left.dir === right.dir;
}

function parseDataViewGroup(value: string): DataViewGroup | null {
  const [field, granularity, extra] = value.split(":");
  if (!field || extra !== undefined) return null;
  if (granularity === undefined || granularity === "") return { field };
  if (!isGroupGranularity(granularity)) return null;
  return { field, granularity };
}

function serializeDataViewGroup(group: DataViewGroup): string {
  return group.granularity
    ? `${group.field}:${group.granularity}`
    : group.field;
}

function parseDataViewGroupStack(value: string): readonly DataViewGroup[] | null {
  if (!value) return [];
  const groups = value.split(",").map(parseDataViewGroup);
  if (groups.some((group) => group === null)) return null;
  return normaliseGroupStack(groups as DataViewGroup[]);
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
  return left.field === right.field && left.granularity === right.granularity;
}

function dataViewGroupStacksEqual(
  left: readonly DataViewGroup[],
  right: readonly DataViewGroup[],
): boolean {
  if (left.length !== right.length) return false;
  return left.every((group, index) => dataViewGroupsEqual(group, right[index]!));
}

function isGroupGranularity(value: string): value is DataViewGroupGranularity {
  return DATA_VIEW_GROUP_GRANULARITIES.includes(
    value as DataViewGroupGranularity,
  );
}

function dataViewFilterFromUnknown(value: unknown): DataViewFilter | null {
  if (!isDataViewFilter(value)) return null;
  return value as DataViewFilter;
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
