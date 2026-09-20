import * as React from "react";
import { rowPublicId, type Row } from "@angee/metadata";
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
  } from "@angee/refine";
import { refineResourceName } from "@angee/metadata";
import {
  useModelMetadata,
} from "@angee/metadata";

import { useValueStable } from "../../lib/use-value-stable";
import type {
  RelationOption,
  RelationSearchState,
} from "../../widgets/RelationField";
import type { RelationFieldInfo } from "../resource/model-metadata-defaults";
import { DEFAULT_PAGE_SIZE } from "../resource/page-size";
import { resolveTextFilterField } from "../resource/resource-view-utils";

export const RELATION_OPTION_LIMIT = 200;

export interface RelationOptionsConfig {
  searchText?: string;
  searchFields?: readonly string[];
  labelField?: string;
  /** Additional scalar fields a composing surface needs from each option row. */
  fields?: readonly string[];
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
  /** Explicit server order for relations whose row sequence is semantic. */
  sorters?: readonly CrudSort[];
}

export interface RelationOptionsList {
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
    resource: resource ? refineResourceName(resource) : "__angee_disabled__",
    dataProviderName: resource?.schemaName,
    id: value ?? "",
    meta: { fields },
    queryOptions: { enabled: Boolean(relation && resource && value) },
  });
  return relationSelectedOption(read.result, labelField);
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

export function useRelationOptions(
  relation: RelationFieldInfo | null,
  config: RelationOptionsConfig = {},
): RelationOptionsResult {
  const {
    enabled = true,
    fields: extraFields,
    filters,
    labelField: optionLabelField,
    pageSize = RELATION_OPTION_LIMIT,
    sort = false,
    sorters,
    searchText,
    searchFields,
  } = config;
  const labelField = optionLabelField ?? relation?.labelField ?? "id";
  const metadata = useModelMetadata(relation?.resource ?? "");
  const defaultSearchField = resolveTextFilterField(metadata);
  const authoredSearchFields = metadata?.resource.recordSearchFields;
  const activeSearchFields =
    searchFields
    ?? (authoredSearchFields?.length ? authoredSearchFields : undefined)
    ?? (defaultSearchField ? [defaultSearchField] : []);
  // Stabilise filters/sorters by VALUE: a consumer that declares them inline
  // (e.g. a board's `laneSource.filters`) rebuilds the array every render, and
  // forwarding a fresh identity into refine's `useList` drives an update loop.
  // A value-equal array keeps a stable identity, so plausible inline props are
  // safe without every caller memoising.
  const searchFilters: CrudFilter[] =
    searchText?.trim() && activeSearchFields.length > 0
      ? [
          {
            operator: "or",
            value: activeSearchFields.map((field) => ({
              field,
              operator: "contains",
              value: searchText.trim(),
            })),
          },
        ]
      : [];
  const stableFilters = useValueStable([...(filters ?? []), ...searchFilters]);
  const stableSorters = useValueStable(sorters);
  const resource = metadata?.resource ?? null;
  const fields = React.useMemo(
    () => refineFieldsFromPaths(["id", labelField, ...(extraFields ?? [])]),
    [extraFields, labelField],
  );
  const run = useList<RowRecord, HttpError>({
    resource: resource ? refineResourceName(resource) : "__angee_disabled__",
    dataProviderName: resource?.schemaName,
    pagination: {
      mode: "server",
      currentPage: 1,
      pageSize: pageSize ?? DEFAULT_PAGE_SIZE,
    },
    ...(stableFilters ? { filters: [...stableFilters] } : {}),
    ...(stableSorters ? { sorters: [...stableSorters] } : {}),
    meta: { fields },
    queryOptions: {
      enabled: enabled && relation !== null && resource !== null,
    },
  });
  const rows = React.useMemo(
    () => (run.result.data ?? []) as readonly Row[],
    [run.result.data],
  );
  const list = React.useMemo<RelationOptionsList>(
    () => ({
      fetching: run.query.isFetching,
      error: run.query.error?.message,
      refetch: () => {
        void run.query.refetch();
      },
    }),
    [run.query],
  );
  const options = React.useMemo(
    () => relationOptionsFromRows(rows, labelField, { sort }),
    [labelField, rows, sort],
  );
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
): RelationOption | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  const row = value as Row;
  const id = rowPublicId(row);
  if (!id) return undefined;
  return { value: id, label: relationOptionLabel(row, labelField, id) };
}

export function relationOptionsFromRows(
  rows: readonly Row[],
  labelField: string,
  config: Pick<RelationOptionsConfig, "sort"> = {},
): readonly RelationOption[] {
  const options = rows.flatMap((row) => {
    const value = rowPublicId(row) ?? "";
    if (!value) return [];
    return [{ value, label: relationOptionLabel(row, labelField, value) }];
  });
  return config.sort
    ? [...options].sort((left, right) => left.label.localeCompare(right.label))
    : options;
}

type RowRecord = BaseRecord & Row;

function relationOptionLabel(
  row: Row,
  labelField: string,
  fallback: string,
): string {
  const label = String(row[labelField] ?? "").trim();
  return label || fallback;
}
