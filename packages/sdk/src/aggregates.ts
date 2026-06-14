import { useMemo } from "react";

import {
  autoExtractAggregate,
  autoExtractGroupBy,
  type AggregateBucket,
  type AggregateMeasure,
} from "./aggregate-extract";
import { DISABLED_DOCUMENTS } from "./disabled-documents";
import { useDocumentQuery } from "./document-query";
import { useModelRootFields } from "./model-metadata";
import { useRegisterModelRefetch } from "./relay-invalidation";
import {
  useStableArray,
  useStableMeasures,
  useStableVariables,
} from "./stable-deps";
import {
  assembleAggregateDocument,
  assembleGroupByDocument,
} from "./selection";
import type {
  ResourceFilter,
  ResourceTypeName,
} from "./__generated__/resource-types";

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
  measures?: readonly AggregateMeasure[];
}

/** The ungrouped total for a model. */
export function useResourceAggregate<
  TName extends ResourceTypeName = ResourceTypeName,
>(
  modelLabel: string,
  options: UseAggregateOptions<TName> = {},
): {
  aggregate: AggregateBucket | null;
  fetching: boolean;
  error: Error | null;
  refetch: () => void;
} {
  const { enabled = true, filter, measures } = options;
  const withFilter = filter !== undefined;
  const stableMeasures = useStableMeasures(measures);
  const rootFields = useModelRootFields(modelLabel);
  const active = enabled && Boolean(modelLabel) && rootFields !== null;

  const document = useMemo(
    () =>
      rootFields
        ? assembleAggregateDocument(modelLabel, rootFields, {
            withFilter,
            measures: stableMeasures,
          })
        : DISABLED_DOCUMENTS.query,
    [modelLabel, rootFields, stableMeasures, withFilter],
  );
  const variables = useStableVariables(
    withFilter ? { filter: filter as Record<string, unknown> } : NO_VARIABLES,
  );
  const run = useDocumentQuery(document, variables, active);
  // Register so a change event (and post-write invalidation) refresh this
  // aggregate the same way the list beside it refreshes — the writes the
  // normalized cache can't see on its own.
  useRegisterModelRefetch(modelLabel, run.refetch, active);
  return {
    aggregate: autoExtractAggregate(run.data, rootFields?.aggregate ?? ""),
    fetching: run.fetching,
    error: run.error,
    refetch: run.refetch,
  };
}

/**
 * A group-by dimension: `field` is the backend enum value to group on
 * (`"STATUS"`), and `key` is the field selected from the returned group key
 * (`"status"`).
 */
export interface GroupByDimension {
  field: string;
  key?: string;
  granularity?: string;
}

export interface GroupByOrder {
  field: string;
  direction: "ASC" | "DESC";
}

export interface UseGroupByOptions<
  TName extends ResourceTypeName = ResourceTypeName,
> extends UseAggregateOptions<TName> {
  dimensions: readonly GroupByDimension[];
  orderBy?: readonly GroupByOrder[];
  page?: number;
  pageSize?: number;
  withFilterEcho?: boolean;
}

function dimensionField(dimension: GroupByDimension): string {
  return dimension.field;
}

function dimensionKey(dimension: GroupByDimension): string {
  return dimension.key ?? dimension.field;
}

/**
 * The group-key value a bucket carries for one dimension. The grouped document
 * selects `key { <dimensionKey> }`, so the bucket stores each value under that
 * exact key — the aggregates layer owns that field-name mapping, not the view.
 */
export function bucketKey(
  bucket: AggregateBucket,
  dimension: GroupByDimension,
): unknown {
  return bucket.key?.[dimensionKey(dimension)] ?? null;
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
  refetch: () => void;
} {
  const {
    dimensions,
    enabled = true,
    filter,
    orderBy,
    page,
    pageSize,
    measures,
    withFilterEcho = false,
  } = options;
  const withFilter = filter !== undefined;
  const rootFields = useModelRootFields(modelLabel);
  const active =
    enabled && Boolean(modelLabel) && dimensions.length > 0 && rootFields !== null;
  const keyFields = useStableArray(dimensions.map(dimensionKey));
  const stableMeasures = useStableMeasures(measures);
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
  const orderVariables = useStableVariables(
    orderBy === undefined ? undefined : { orderBy },
  );
  const withOrderBy = orderVariables.orderBy !== undefined;
  const variables = useStableVariables({
    groupBy,
    pagination: paginationVariables(page, pageSize) ?? null,
    ...(withFilter ? { filter } : {}),
    ...(withOrderBy ? { orderBy: orderVariables.orderBy } : {}),
  });

  const document = useMemo(
    () =>
      rootFields
        ? assembleGroupByDocument(modelLabel, rootFields, {
            keyFields,
            measures: stableMeasures,
            withFilter,
            withOrderBy,
            withFilterEcho,
          })
        : DISABLED_DOCUMENTS.query,
    [
      modelLabel,
      rootFields,
      keyFields,
      stableMeasures,
      withFilter,
      withOrderBy,
      withFilterEcho,
    ],
  );

  const run = useDocumentQuery(document, variables, active);
  useRegisterModelRefetch(modelLabel, run.refetch, active);
  const result = autoExtractGroupBy(run.data, rootFields?.groupBy ?? "");
  return {
    count: result.count,
    totalCount: result.totalCount,
    buckets: result.buckets,
    fetching: run.fetching,
    error: run.error,
    refetch: run.refetch,
  };
}
