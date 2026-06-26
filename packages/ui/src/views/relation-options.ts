import * as React from "react";
import {
  rowPublicId,
  type Row,
} from "@angee/resources";
import {
  useList,
  type BaseRecord,
  type HttpError,
  } from "@refinedev/core";
import {
  DEFAULT_PAGE_SIZE,
  refineFieldsFromPaths,
  } from "@angee/refine";
import {
  refineResourceName,
} from "@angee/resources";
import {
  useModelMetadata,
} from "@angee/resources";

import type { RelationOption } from "../widgets/RelationField";
import type { RelationFieldInfo } from "./model-metadata-defaults";

export const RELATION_OPTION_LIMIT = 200;

export interface RelationOptionsConfig {
  labelField?: string;
  pageSize?: number;
  enabled?: boolean;
  sort?: boolean;
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
    labelField: optionLabelField,
    pageSize = RELATION_OPTION_LIMIT,
    sort = false,
  } = config;
  const labelField = optionLabelField ?? relation?.labelField ?? "id";
  const metadata = useModelMetadata(relation?.resource ?? "");
  const resource = metadata?.resource ?? null;
  const fields = React.useMemo(
    () => refineFieldsFromPaths([labelField]),
    [labelField],
  );
  const run = useList<RowRecord, HttpError>({
    resource: resource ? refineResourceName(resource) : "__angee_disabled__",
    dataProviderName: resource?.schemaName,
    pagination: {
      mode: "server",
      currentPage: 1,
      pageSize: pageSize ?? DEFAULT_PAGE_SIZE,
    },
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
