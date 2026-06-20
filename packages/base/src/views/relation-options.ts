import * as React from "react";
import {
  useResourceList,
  rowPublicId,
  type Row,
  type UseResourceListResult,
} from "@angee/sdk";

import type { RelationOption } from "../widgets/RelationField";
import type { RelationFieldInfo } from "./model-metadata-defaults";

export const RELATION_OPTION_LIMIT = 200;

export interface RelationOptionsConfig {
  labelField?: string;
  pageSize?: number;
  enabled?: boolean;
  sort?: boolean;
}

export interface RelationOptionsResult {
  list: UseResourceListResult;
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
  const list = useResourceList(relation?.model ?? "", {
    fields: [labelField],
    pageSize,
    enabled: enabled && relation !== null,
  });
  const options = React.useMemo(
    () => relationOptionsFromRows(list.rows, labelField, { sort }),
    [labelField, list.rows, sort],
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

function relationOptionLabel(
  row: Row,
  labelField: string,
  fallback: string,
): string {
  const label = String(row[labelField] ?? "").trim();
  return label || fallback;
}
