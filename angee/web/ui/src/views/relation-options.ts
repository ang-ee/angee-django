import * as React from "react";
import {
  rowPublicId,
  type Row,
} from "@angee/metadata";
import {
  useList,
  type BaseRecord,
  type CrudFilter,
  type HttpError,
  } from "@refinedev/core";
import {
  refineFieldsFromPaths,
  } from "@angee/refine";
import {
  refineResourceName,
} from "@angee/metadata";
import {
  archiveFilterField,
  useModelMetadata,
} from "@angee/metadata";

import type { RelationOption } from "../widgets/RelationField";
import type { RelationFieldInfo } from "./model-metadata-defaults";
import { DEFAULT_PAGE_SIZE } from "./page-size";

export const RELATION_OPTION_LIMIT = 200;

export interface RelationOptionsConfig {
  labelField?: string;
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
}

export interface RelationOptionsList {
  fetching: boolean;
  refetch: () => void;
}

export interface RelationOptionsResult {
  list: RelationOptionsList;
  options: readonly RelationOption[];
}

export function useRelationOptions(
  relation: RelationFieldInfo | null,
  config: RelationOptionsConfig = {},
): RelationOptionsResult {
  const {
    enabled = true,
    filters,
    labelField: optionLabelField,
    pageSize = RELATION_OPTION_LIMIT,
    sort = false,
  } = config;
  const labelField = optionLabelField ?? relation?.labelField ?? "id";
  const metadata = useModelMetadata(relation?.resource ?? "");
  const resource = metadata?.resource ?? null;
  const fields = React.useMemo(
    () => refineFieldsFromPaths(["id", labelField]),
    [labelField],
  );
  const archiveField = archiveFilterField(metadata);
  const effectiveFilters = React.useMemo(
    () => relationOptionFilters(filters, archiveField),
    [archiveField, filters],
  );
  const run = useList<RowRecord, HttpError>({
    resource: resource ? refineResourceName(resource) : "__angee_disabled__",
    dataProviderName: resource?.schemaName,
    pagination: {
      mode: "server",
      currentPage: 1,
      pageSize: pageSize ?? DEFAULT_PAGE_SIZE,
    },
    ...(effectiveFilters ? { filters: [...effectiveFilters] } : {}),
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
  return React.useMemo(() => ({ list, options }), [list, options]);
}

/**
 * The picker's option filters with the unarchived default folded in. An
 * archivable target offers only unarchived rows — the picker's analog of the
 * list default: surfacing archived records is the archived facet's job, not the
 * picker's. An explicit caller filter on the flag wins; already-linked archived
 * records still resolve (the selected option reads from the parent record, not
 * this list).
 */
export function relationOptionFilters(
  filters: readonly CrudFilter[] | undefined,
  archiveField: string | null,
): readonly CrudFilter[] | undefined {
  if (!archiveField) return filters;
  if (filters?.some((item) => "field" in item && item.field === archiveField)) {
    return filters;
  }
  return [...(filters ?? []), { field: archiveField, operator: "eq", value: false }];
}

/**
 * The selected-record analog of {@link relationOptionsFromRows}: the picker
 * option for a single already-loaded related record (the parent read's folded
 * `{ id, <labelField> }` object), reusing the shared labeling rule so the
 * trigger shows the label with no extra round-trip. Returns `undefined` when the
 * value carries no readable id (e.g. a bare id string before a label loads).
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
