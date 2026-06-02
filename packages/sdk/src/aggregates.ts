import { useMemo } from "react";

import {
  autoExtractAggregate,
  autoExtractGroupBy,
  type AggregateBucket,
} from "./aggregate-extract";
import { useDocumentQuery } from "./document-query";
import { useStableArray, useStableVariables } from "./stable-deps";
import {
  aggregateFieldName,
  assembleAggregateDocument,
  assembleGroupByDocument,
  groupByFieldName,
} from "./selection";
import type {
  ResourceFilter,
  ResourceTypeName,
} from "./__generated__/resource-types";

export type { AggregateBucket, GroupByResult } from "./aggregate-extract";

// Stable empty variables for the ungrouped query, so the hook does not re-run on
// every render.
const NO_VARIABLES: Record<string, unknown> = {};

/** A filter accepted as the model's generated input or any record. */
type Filter<TName extends ResourceTypeName> =
  | ResourceFilter<TName>
  | Record<string, unknown>;

export interface UseAggregateOptions<
  TName extends ResourceTypeName = ResourceTypeName,
> {
  enabled?: boolean;
  filter?: Filter<TName>;
}

/** The ungrouped total for a model. */
export function useAggregateQuery<
  TName extends ResourceTypeName = ResourceTypeName,
>(
  modelLabel: string,
  options: UseAggregateOptions<TName> = {},
): { aggregate: AggregateBucket | null; fetching: boolean; error: Error | null } {
  const { enabled = true, filter } = options;
  const active = enabled && Boolean(modelLabel);
  const withFilter = filter !== undefined;

  const document = useMemo(
    () => assembleAggregateDocument(modelLabel, { withFilter }),
    [modelLabel, withFilter],
  );
  const variables = useStableVariables(
    withFilter ? { filter: filter as Record<string, unknown> } : NO_VARIABLES,
  );
  const run = useDocumentQuery(document, variables, active);
  return {
    aggregate: autoExtractAggregate(run.data, aggregateFieldName(modelLabel)),
    fetching: run.fetching,
    error: run.error,
  };
}

/**
 * A group-by dimension: `field` is the backend enum value to group on
 * (`"STATUS"`), and `key` is the field selected from the returned group key
 * (`"status"`). `by` is accepted for callers still passing the previous name.
 */
export interface GroupByDimension {
  field: string;
  key?: string;
  by?: string;
  granularity?: string;
}

export interface UseGroupByOptions<
  TName extends ResourceTypeName = ResourceTypeName,
> extends UseAggregateOptions<TName> {
  dimensions: readonly GroupByDimension[];
  page?: number;
  pageSize?: number;
  withFilterEcho?: boolean;
}

function dimensionField(dimension: GroupByDimension): string {
  return dimension.by ?? dimension.field;
}

function dimensionKey(dimension: GroupByDimension): string {
  return dimension.key ?? dimension.field;
}

function paginationVariables(
  page: number | undefined,
  pageSize: number | undefined,
): Record<string, number> | undefined {
  if (pageSize === undefined) return undefined;
  const safePage = Math.max(1, Math.floor(page ?? 1));
  const limit = Math.max(1, Math.floor(pageSize));
  return { offset: (safePage - 1) * limit, limit };
}

/** Grouped totals for a model: one bucket per distinct group key. */
export function useResourceGroupBy<
  TName extends ResourceTypeName = ResourceTypeName,
>(
  modelLabel: string,
  options: UseGroupByOptions<TName>,
): {
  count: number;
  totalCount: number;
  buckets: readonly AggregateBucket[];
  fetching: boolean;
  error: Error | null;
} {
  const {
    dimensions,
    enabled = true,
    filter,
    page,
    pageSize,
    withFilterEcho = false,
  } = options;
  const active = enabled && Boolean(modelLabel) && dimensions.length > 0;
  const withFilter = filter !== undefined;
  const keyFields = useStableArray(dimensions.map(dimensionKey));
  const groupBy = useMemo(
    () =>
      dimensions.map((dimension) => ({
        field: dimensionField(dimension),
        ...(dimension.granularity
          ? { granularity: dimension.granularity }
          : {}),
      })),
    [dimensions],
  );
  const variables = useStableVariables({
    groupBy,
    pagination: paginationVariables(page, pageSize) ?? null,
    ...(withFilter ? { filter } : {}),
  });

  const document = useMemo(
    () =>
      assembleGroupByDocument(modelLabel, {
        keyFields,
        withFilter,
        withFilterEcho,
      }),
    [modelLabel, keyFields, withFilter, withFilterEcho],
  );

  const run = useDocumentQuery(document, variables, active);
  const result = autoExtractGroupBy(run.data, groupByFieldName(modelLabel));
  return {
    count: result.count,
    totalCount: result.totalCount,
    buckets: result.buckets,
    fetching: run.fetching,
    error: run.error,
  };
}
