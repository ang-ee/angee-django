import * as React from "react";
import {
  ResourceQuery,
  modelFieldForPath,
  refineResourceName,
  rowPublicId,
  rowValueAtPath,
  useModelMetadata,
  useSchemaFieldMetadata,
  type Row,
} from "@angee/metadata";
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
  stableSerialize,
  type ListQueryFieldArgument,
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
import { enumValueLabel, statusLabel } from "../../lib/labels";
import { isTone, type Tone } from "../../lib/tones";
import { useStatusTone } from "../../widgets/use-status-tone";

export const RELATION_OPTION_LIMIT = 200;

export interface RelationOptionsConfig {
  searchText?: string;
  searchFields?: readonly string[];
  labelField?: string;
  /** Additional scalar fields a composing surface needs from each option row. */
  fields?: readonly string[];
  /** Related-row field rendered as secondary option text. */
  optionDescriptionField?: string;
  /** Related-row enum/status field rendered as a shared-tone badge. */
  optionStatusField?: string;
  /** Context values required by the computed status field. */
  optionStatusArguments?: readonly ListQueryFieldArgument[];
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
  extends Omit<RelationOptionsConfig, "enabled" | "searchText" | "page"> {
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

/** Resolve a selected identity independently of the option page or search.
 * @owner RelationFieldWidget
 */
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
  return React.useMemo(() => relationSelectedOption(read.result, labelField), [read.result, labelField]);
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
  const queryKey = stableSerialize([relation, optionsConfig, searchText]);
  const [pagination, setPagination] = React.useState({ key: queryKey, page: 1 });
  const page = pagination.key === queryKey ? pagination.page : 1;
  const pageSize = optionsConfig.pageSize ?? RELATION_OPTION_LIMIT;
  const setPage = React.useCallback((next: number) => {
    setPagination({ key: queryKey, page: next });
  }, [queryKey]);
  const suppliedSelected = supplied?.value === value ? supplied : undefined;
  const resolved = useRelationSelectedOption(
    relation,
    suppliedSelected ? undefined : value,
  );
  const selected = React.useMemo(() => suppliedSelected ?? (resolved?.value === value ? resolved : undefined)
    ?? (value ? { value, label: value } : undefined), [resolved, suppliedSelected, value]);
  const result = useRelationOptions(relation, {
    ...optionsConfig, page,
    enabled: opened,
    searchText,
  });
  React.useEffect(() => {
    if (result.list.fetching || result.list.error || result.list.total === undefined) return;
    const last = Math.max(1, Math.ceil(result.list.total / pageSize));
    if (page > last) setPage(last);
  }, [page, pageSize, result.list.error, result.list.fetching, result.list.total, setPage]);
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
      pagination: { page, pageSize, total: result.list.total, onPageChange: setPage },
    }),
    [page, pageSize, result.list, search, searchText, setPage],
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

/** Search executable text fields on the server, otherwise labels in the loaded page.
 * @owner RelationFieldWidget
 */
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
    optionDescriptionField,
    optionStatusField,
    optionStatusArguments,
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
  const schemaMetadata = useSchemaFieldMetadata();
  const resolveStatusTone = useStatusTone();
  const statusMetadata = React.useMemo(
    () => metadata && optionStatusField
      ? modelFieldForPath(optionStatusField, metadata, schemaMetadata)?.field
      : undefined,
    [metadata, optionStatusField, schemaMetadata],
  );
  const statusTones = React.useMemo<Record<string, Tone> | undefined>(() => {
    const entries = Object.entries(statusMetadata?.tones ?? {}).filter((entry): entry is [string, Tone] => isTone(entry[1]));
    return entries.length ? Object.fromEntries(entries) : undefined;
  }, [statusMetadata]);
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
  const stableFields = useValueStable([...new Set([
    ...(extraFields ?? []),
    ...(optionDescriptionField ? [optionDescriptionField] : []),
    ...(optionStatusField ? [optionStatusField] : []),
  ])]);
  const stableWhere = useValueStable(where);
  const stableStatusArguments = useValueStable(optionStatusArguments);
  const resource = metadata?.resource ?? null;
  const selectionPaths = React.useMemo(() => [...new Set([
    "id", labelField, ...(colorField ? [colorField] : []), ...stableFields,
  ])], [stableFields, labelField, colorField]);
  const fields = React.useMemo(
    () => refineFieldsFromPaths(selectionPaths),
    [selectionPaths],
  );
  const meta = React.useMemo(() => {
    if (!stableWhere && (!optionStatusField || !stableStatusArguments?.length)) return { fields };
    const target = resource && listBatchTarget(resource);
    if (!target) throw new Error("A filtered or context-bound relation requires a list query contract.");
    return listQueryMeta(target, selectionPaths, stableWhere, undefined,
      optionStatusField && stableStatusArguments?.length
        ? [{ path: optionStatusField, arguments: stableStatusArguments }]
        : []);
  }, [fields, optionStatusField, resource, selectionPaths, stableStatusArguments, stableWhere]);
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
    const options = relationOptionsFromRows(rows, labelField, {
      sort,
      colorField,
      descriptionField: optionDescriptionField,
      statusField: optionStatusField,
      statusLabel: (value) => {
        const option = statusMetadata?.values?.find((item) => item.value === value);
        return option ? enumValueLabel(option) : statusLabel(value);
      },
      statusTone: (value) => resolveStatusTone(value, statusTones),
    });
    return labelSearch
      ? options.filter((option) => `${option.label} ${option.description ?? ""} ${option.status?.label ?? ""}`
        .toLocaleLowerCase().includes(labelSearch))
      : options;
  }, [colorField, labelField, labelSearch, optionDescriptionField, optionStatusField, resolveStatusTone, rows, sort, statusMetadata, statusTones]);
  return React.useMemo(() => ({ list, options, rows }), [list, options, rows]);
}

/**
 * The selected-record analog of {@link relationOptionsFromRows}: the picker
 * option for a single already-loaded related record (the parent read's folded
 * `{ id, <labelField> }` object), reusing the shared labeling rule so the
 * trigger shows the label with no extra round-trip. Returns `undefined` when the
 * value carries no readable row (for example an inaccessible bare id).
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
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

/**
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function relationOptionsFromRows(
  rows: readonly Row[],
  labelField: string,
  config: Pick<RelationOptionsConfig, "sort"> & {
    colorField?: string;
    descriptionField?: string;
    statusField?: string;
    statusLabel?: (value: string) => string;
    statusTone?: (value: string) => Tone;
  } = {},
): readonly RelationOption[] {
  const options = rows.flatMap((row) => {
    const value = rowPublicId(row) ?? "";
    if (!value) return [];
    const descriptionValue = config.descriptionField ? rowValueAtPath(row, config.descriptionField) : undefined;
    const description = descriptionValue == null ? "" : String(descriptionValue).trim();
    const statusValue = config.statusField ? rowValueAtPath(row, config.statusField) : undefined;
    const status = statusValue == null ? "" : String(statusValue).trim();
    return [{
      value,
      label: relationOptionLabel(row, labelField, value),
      ...relationOptionColor(row, config.colorField),
      ...(description ? { description } : {}),
      ...(status ? { status: {
        value: status,
        label: config.statusLabel?.(status) ?? statusLabel(status),
        tone: config.statusTone?.(status) ?? "brand",
      } } : {}),
    }];
  });
  return config.sort
    ? [...options].sort((left, right) => left.label.localeCompare(right.label))
    : options;
}

type RowRecord = BaseRecord & Row;

function relationOptionColor(row: Row, colorField: string | undefined): { color?: string } {
  const color = colorField ? hexColor(rowValueAtPath(row, colorField)) : undefined;
  return color ? { color } : {};
}

function relationOptionLabel(
  row: Row,
  labelField: string,
  fallback: string,
): string {
  const label = String(rowValueAtPath(row, labelField) ?? "").trim();
  return label || fallback;
}
