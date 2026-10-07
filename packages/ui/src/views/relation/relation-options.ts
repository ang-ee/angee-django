import * as React from "react";
import { ResourceQuery, refineResourceName, rowPublicId, useModelMetadata, type Row } from "@angee/metadata";
import {
  useList,
  useOne,
  type BaseRecord,
  type CrudFilter,
  type CrudSort,
  type HttpError,
  } from "@refinedev/core";
import { useDebounce } from "use-debounce";
import {
  refineFieldsFromPaths,
  listQueryMeta,
  } from "@angee/refine";
import { listBatchTarget } from "../resource/resource-operations";
import { hexColor } from "../../lib/hex-color";
import { useValueStable } from "../../lib/use-value-stable";
import type {
  RelationOption,
  RelationSearchState,
} from "../../widgets/RelationField";
import type { RelationFieldInfo } from "../resource/model-metadata-defaults";
import { DEFAULT_PAGE_SIZE } from "../resource/page-size";

export const RELATION_OPTION_LIMIT = 200;

export interface RelationOptionsConfig {
  searchText?: string;
  searchFields?: readonly string[];
  labelField?: string;
  /** Additional scalar fields a composing surface needs from each option row. */
  fields?: readonly string[];
  /** One-based server page, shared by pickers and row catalogues. */
  page?: number;
  pageSize?: number;
  enabled?: boolean;
  sort?: boolean;
  /**
   * Server-side filters narrowing which related rows are offered — for a
   * relation whose target holds more kinds of row than the field accepts (e.g.
   * only `app_keys` credentials). Omitted, the picker offers every row the
   * resource's own queryset exposes.
   */
  filters?: readonly CrudFilter[];
  where?: Record<string, unknown>;
  /** Explicit server order for relations whose row sequence is semantic. */
  sorters?: readonly CrudSort[];
}

export interface RelationOptionsList {
  total?: number;
  error?: string;
  fetching: boolean;
  refetch: () => void;
}

export interface RelationOptionsResult {
  list: RelationOptionsList;
  options: readonly RelationOption[];
  rows: readonly Row[];
}

export interface RelationPickerOptionsConfig
  extends Omit<RelationOptionsConfig, "enabled" | "searchText"> {
  value?: string | null;
  /** Folded selected row supplied by a parent record read, when available. */
  selectedOption?: RelationOption;
}

export interface RelationPickerOptionsResult extends RelationOptionsResult {
  activate: () => void;
  onOpenChange: (open: boolean) => void;
  onSearchChange: (query: string) => void;
  searchState: RelationSearchState;
}

/** Resolve a selected identity independently of the option page or search. */
export function useRelationSelectedOption(
  relation: RelationFieldInfo | null,
  value: string | null | undefined,
): RelationOption | undefined {
  const metadata = useModelMetadata(relation?.resource ?? "");
  const resource = metadata?.resource;
  const labelField = relation?.labelField ?? "id";
  const fields = React.useMemo(
    () => refineFieldsFromPaths(["id", labelField]),
    [labelField],
  );
  const read = useOne<RowRecord, HttpError>({
    resource: refineResourceName(resource),
    dataProviderName: resource?.schemaName,
    id: value ?? "",
    meta: { fields },
    queryOptions: { enabled: Boolean(relation && resource && value) },
  });
  return relationSelectedOption(read.result, labelField);
}

/**
 * Labels for selected ids that neither the loaded option page nor the value's own
 * records label (ids an action seeded, or a page beyond the first), read in one
 * bounded list query. The plural of {@link useRelationSelectedOption}.
 */
export function useRelationSelectedOptions(
  relation: RelationFieldInfo | null,
  ids: readonly string[],
  known: readonly RelationOption[],
): readonly RelationOption[] {
  const missing = React.useMemo(
    () => ids.filter((id) => !known.some((option) => option.value === id)),
    [ids, known],
  );
  const filters = React.useMemo<CrudFilter[]>(
    () => (missing.length > 0 ? [{ field: "id", operator: "in", value: missing }] : []),
    [missing],
  );
  const { options } = useRelationOptions(relation, {
    enabled: missing.length > 0,
    filters,
    pageSize: Math.max(missing.length, 1),
  });
  return options;
}

/** Own lazy remote search plus selected-record retention for a relation picker. */
export function useRelationPickerOptions(
  relation: RelationFieldInfo | null,
  config: RelationPickerOptionsConfig = {},
): RelationPickerOptionsResult {
  const { selectedOption: supplied, value, ...optionsConfig } = config;
  const [opened, setOpened] = React.useState(false);
  const [search, setSearch] = React.useState("");
  const [searchText] = useDebounce(search, 250);
  const suppliedSelected = supplied?.value === value ? supplied : undefined;
  const resolved = useRelationSelectedOption(
    relation,
    suppliedSelected ? undefined : value,
  );
  const selected = suppliedSelected ?? (resolved?.value === value ? resolved : undefined)
    ?? (value ? { value, label: value } : undefined);
  const result = useRelationOptions(relation, {
    ...optionsConfig,
    enabled: opened,
    searchText,
  });
  const options = React.useMemo(
    () =>
      selected &&
      !result.options.some((option) => option.value === selected.value)
        ? [selected, ...result.options]
        : result.options,
    [result.options, selected],
  );
  const activate = React.useCallback(() => setOpened(true), []);
  const onOpenChange = React.useCallback((open: boolean) => {
    setSearch("");
    if (open) setOpened(true);
  }, []);
  const searchState = React.useMemo<RelationSearchState>(
    () => ({
      pending: result.list.fetching || search !== searchText,
      error: result.list.error,
      retry: result.list.refetch,
    }),
    [result.list, search, searchText],
  );
  return React.useMemo(
    () => ({
      ...result,
      options,
      activate,
      onOpenChange,
      onSearchChange: setSearch,
      searchState,
    }),
    [activate, onOpenChange, options, result, searchState],
  );
}

/** Search executable text fields on the server, otherwise labels in the loaded page. */
export function useRelationOptions(
  relation: RelationFieldInfo | null,
  config: RelationOptionsConfig = {},
): RelationOptionsResult {
  const {
    enabled = true,
    fields: extraFields,
    filters,
    where,
    labelField: optionLabelField,
    page = 1,
    pageSize = RELATION_OPTION_LIMIT,
    sort = false,
    sorters,
    searchText,
    searchFields,
  } = config;
  const labelField = optionLabelField ?? relation?.labelField ?? "id";
  // A model's record colour rides along with its label, so its chips can wear it.
  const colorField = optionLabelField ? undefined : relation?.colorField;
  const metadata = useModelMetadata(relation?.resource ?? "");
  const activeSearchFields = metadata
    ? ResourceQuery.from(metadata).textSearchFields(searchFields)
    : [];
  const text = searchText?.trim() ?? "";
  const labelSearch = activeSearchFields.length > 0
    ? ""
    : text.toLocaleLowerCase();
  // Stabilise filters/sorters by VALUE: a consumer that declares them inline
  // (e.g. a board's `laneSource.filters`) rebuilds the array every render, and
  // forwarding a fresh identity into refine's `useList` drives an update loop.
  // A value-equal array keeps a stable identity, so plausible inline props are
  // safe without every caller memoising.
  const searchFilters: CrudFilter[] =
    text && activeSearchFields.length > 0
      ? [
          {
            operator: "or",
            value: activeSearchFields.map((field) => ({
              field,
              operator: "contains",
              value: text,
            })),
          },
        ]
      : [];
  const stableFilters = useValueStable([...(filters ?? []), ...searchFilters]);
  const stableSorters = useValueStable(sorters);
  const stableFields = useValueStable(extraFields);
  const stableWhere = useValueStable(where);
  const resource = metadata?.resource ?? null;
  const fields = React.useMemo(
    () => refineFieldsFromPaths(["id", labelField, ...(colorField ? [colorField] : []), ...(stableFields ?? [])]),
    [stableFields, labelField, colorField],
  );
  const meta = React.useMemo(() => {
    if (!stableWhere) return { fields };
    const target = resource && listBatchTarget(resource);
    if (!target) throw new Error("A filtered relation requires a list query contract.");
    return listQueryMeta(target, ["id", labelField, ...(stableFields ?? [])], stableWhere);
  }, [fields, resource, labelField, stableFields, stableWhere]);
  const run = useList<RowRecord, HttpError>({
    resource: refineResourceName(resource),
    dataProviderName: resource?.schemaName,
    pagination: {
      mode: "server",
      currentPage: page,
      pageSize: pageSize ?? DEFAULT_PAGE_SIZE,
    },
    ...(stableFilters ? { filters: [...stableFilters] } : {}),
    ...(stableSorters ? { sorters: [...stableSorters] } : {}),
    meta,
    queryOptions: {
      enabled: enabled && relation !== null && resource !== null,
    },
  });
  const rows = React.useMemo(
    () => (run.result.data ?? []) as readonly Row[],
    [run.result.data],
  );
  const queryRefetch = run.query.refetch;
  const refetch = React.useCallback(() => {
    void queryRefetch();
  }, [queryRefetch]);
  const list = React.useMemo<RelationOptionsList>(
    () => ({
      total: run.result.total,
      fetching: run.query.isFetching,
      error: run.query.error?.message,
      refetch,
    }),
    [run.query.isFetching, run.query.error?.message, refetch, run.result.total],
  );
  const options = React.useMemo(() => {
    const options = relationOptionsFromRows(rows, labelField, { sort, colorField });
    return labelSearch
      ? options.filter((option) => option.label.toLocaleLowerCase().includes(labelSearch))
      : options;
  }, [colorField, labelField, labelSearch, rows, sort]);
  return React.useMemo(() => ({ list, options, rows }), [list, options, rows]);
}

/**
 * The selected-record analog of {@link relationOptionsFromRows}: the picker
 * option for a single already-loaded related record (the parent read's folded
 * `{ id, <labelField> }` object), reusing the shared labeling rule so the
 * trigger shows the label with no extra round-trip. Returns `undefined` when the
 * value carries no readable row (for example an inaccessible bare id).
 */
export function relationSelectedOption(
  value: unknown,
  labelField: string,
  colorField?: string,
): RelationOption | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  const row = value as Row;
  const id = rowPublicId(row);
  if (!id) return undefined;
  return { value: id, label: relationOptionLabel(row, labelField, id), ...relationOptionColor(row, colorField) };
}

export function relationOptionsFromRows(
  rows: readonly Row[],
  labelField: string,
  config: Pick<RelationOptionsConfig, "sort"> & { colorField?: string } = {},
): readonly RelationOption[] {
  const options = rows.flatMap((row) => {
    const value = rowPublicId(row) ?? "";
    if (!value) return [];
    return [{ value, label: relationOptionLabel(row, labelField, value), ...relationOptionColor(row, config.colorField) }];
  });
  return config.sort
    ? [...options].sort((left, right) => left.label.localeCompare(right.label))
    : options;
}

type RowRecord = BaseRecord & Row;

function relationOptionColor(row: Row, colorField: string | undefined): { color?: string } {
  const color = colorField ? hexColor(row[colorField]) : undefined;
  return color ? { color } : {};
}

function relationOptionLabel(
  row: Row,
  labelField: string,
  fallback: string,
): string {
  const label = String(row[labelField] ?? "").trim();
  return label || fallback;
}
