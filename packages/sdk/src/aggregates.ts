import { useMemo } from "react";

import {
  autoExtractAggregate,
  autoExtractGroupBy,
  type AggregateBucket,
  type AggregateMeasure,
} from "./aggregate-extract";
import {
  dataQueryGroupField,
  dataQueryGroupKey,
  useGraphQLDataSource,
  type DataQueryGroup,
  type DataQueryGroupOrder,
} from "./data";
import { useDocumentQuery } from "./document-query";
import { useRegisterModelRefetch } from "./relay-invalidation";
import {
  useStableMeasures,
  useStableValue,
  useStableVariables,
} from "./stable-deps";
import type {
  ResourceFilter,
  ResourceTypeName,
} from "./resource-types";

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
  const source = useGraphQLDataSource(modelLabel);
  const active = enabled && Boolean(modelLabel) && source.canQuery;

  const document = useMemo(
    () =>
      source.aggregateDocument({
        ...(withFilter ? { filter: {} } : {}),
        measures: stableMeasures,
      }),
    [source, stableMeasures, withFilter],
  );
  const variables = useStableVariables(
    source.aggregateVariables(withFilter ? { filter } : undefined),
  );
  const run = useDocumentQuery(document, variables, active);
  // Register so a change event (and post-write invalidation) refresh this
  // aggregate the same way the list beside it refreshes — the writes the
  // normalized cache can't see on its own.
  useRegisterModelRefetch(modelLabel, run.refetch, active);
  return {
    aggregate: autoExtractAggregate(run.data, source.rootFields?.aggregate ?? ""),
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
export type GroupByDimension = DataQueryGroup;

export type GroupByOrder = DataQueryGroupOrder;

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
  return dataQueryGroupField(dimension);
}

function dimensionKey(dimension: GroupByDimension): string {
  return dataQueryGroupKey(dimension);
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
  const source = useGraphQLDataSource(modelLabel);
  const active =
    enabled && Boolean(modelLabel) && dimensions.length > 0 && source.canQuery;
  const stableMeasures = useStableMeasures(measures);
  const groups = useStableValue<readonly GroupByDimension[]>(
    dimensions.map((dimension) => ({
      field: dimensionField(dimension),
      ...(dimension.key ? { key: dimension.key } : {}),
      ...(dimension.granularity
        ? { granularity: dimension.granularity }
        : {}),
    })),
    [],
  );
  const stableOrderBy = useStableValue<readonly GroupByOrder[]>(orderBy, []);
  const withOrderBy = orderBy !== undefined;
  const variables = useStableVariables(
    source.groupByVariables({
      groups,
      page,
      pageSize,
      measures: stableMeasures,
      ...(withFilter ? { filter } : {}),
      ...(withOrderBy ? { groupOrder: stableOrderBy } : {}),
    }),
  );

  const document = useMemo(
    () =>
      source.groupByDocument(
        {
          groups,
          measures: stableMeasures,
          ...(withFilter ? { filter: {} } : {}),
          ...(withOrderBy ? { groupOrder: stableOrderBy } : {}),
        },
        { withFilterEcho },
      ),
    [
      source,
      groups,
      stableMeasures,
      withFilter,
      withOrderBy,
      stableOrderBy,
      withFilterEcho,
    ],
  );

  const run = useDocumentQuery(document, variables, active);
  useRegisterModelRefetch(modelLabel, run.refetch, active);
  const result = autoExtractGroupBy(run.data, source.rootFields?.groupBy ?? "");
  return {
    count: result.count,
    totalCount: result.totalCount,
    buckets: result.buckets,
    fetching: run.fetching,
    error: run.error,
    refetch: run.refetch,
  };
}
