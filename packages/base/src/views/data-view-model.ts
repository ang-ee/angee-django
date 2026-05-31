import {
  createLoader,
  createParser,
  createSerializer,
  parseAsArrayOf,
  parseAsInteger,
  parseAsJson,
  parseAsString,
  parseAsStringLiteral,
  type LoaderInput,
  type inferParserType,
} from "nuqs";
import {
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
export const DEFAULT_DATA_VIEW_PAGE_SIZE = 50;

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
  selection: ReadonlySet<string>;
  view: DataViewKind;
}

export interface DataViewInitialState {
  page?: number;
  pageSize?: number;
  sort?: DataViewSort | null;
  filter?: DataViewFilter;
  group?: DataViewGroup | null;
  selection?: Iterable<string>;
  view?: DataViewKind;
}

export type DataViewAction =
  | { type: "setPage"; page: number }
  | { type: "setPageSize"; pageSize: number }
  | { type: "setSort"; sort: DataViewSort | null }
  | { type: "setFilter"; filter: DataViewFilter }
  | { type: "setGroup"; group: DataViewGroup | null }
  | { type: "setSelection"; selection: Iterable<string> }
  | { type: "toggleSelection"; id: string; selected?: boolean }
  | { type: "clearSelection" }
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

export const dataViewFilterParser =
  parseAsJson<DataViewFilter>(dataViewFilterFromUnknown);

export const dataViewQueryParsers = {
  page: parseAsInteger.withDefault(1),
  pageSize: parseAsInteger.withDefault(DEFAULT_DATA_VIEW_PAGE_SIZE),
  sort: dataViewSortParser,
  filter: dataViewFilterParser,
  group: dataViewGroupParser,
  selection: parseAsArrayOf(parseAsString).withDefault([]),
  view: parseAsStringLiteral(DATA_VIEW_KINDS).withDefault("list"),
};

export type DataViewQueryValues = inferParserType<typeof dataViewQueryParsers>;

const loadDataViewQuery = createLoader(dataViewQueryParsers);
const serializeDataViewQuery = createSerializer(dataViewQueryParsers);

export function createDataViewState(
  initial: DataViewInitialState = {},
): DataViewState {
  return {
    page: normalisePage(initial.page),
    pageSize: clampPageSize(
      initial.pageSize ?? DEFAULT_DATA_VIEW_PAGE_SIZE,
    ),
    sort: initial.sort ? normaliseSort(initial.sort) : null,
    filter: normaliseFilter(initial.filter),
    group: initial.group ? normaliseGroup(initial.group) : null,
    selection: new Set(initial.selection ?? []),
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
      });
    case "setSelection":
      return { ...state, selection: new Set(action.selection) };
    case "toggleSelection":
      return { ...state, selection: toggledSelection(state.selection, action) };
    case "clearSelection":
      return { ...state, selection: new Set() };
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
    selection: [...state.selection],
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
    selection: values.selection.length > 0 ? values.selection : base.selection,
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
    initialPage: state.page,
  };
  if (hasFilter(state.filter)) options.filter = state.filter;
  const order = dataViewSortToResourceOrder(state.sort);
  if (order) options.order = order;
  if (input.enabled !== undefined) options.enabled = input.enabled;
  return options;
}

function resetQueryScope(state: DataViewState): DataViewState {
  return { ...state, page: 1, selection: new Set() };
}

function toggledSelection(
  selection: ReadonlySet<string>,
  action: Extract<DataViewAction, { type: "toggleSelection" }>,
): ReadonlySet<string> {
  const next = new Set(selection);
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

function dataViewGroupsEqual(
  left: DataViewGroup,
  right: DataViewGroup,
): boolean {
  return left.field === right.field && left.granularity === right.granularity;
}

function isGroupGranularity(value: string): value is DataViewGroupGranularity {
  return DATA_VIEW_GROUP_GRANULARITIES.includes(
    value as DataViewGroupGranularity,
  );
}

function dataViewFilterFromUnknown(value: unknown): DataViewFilter | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as DataViewFilter;
}
