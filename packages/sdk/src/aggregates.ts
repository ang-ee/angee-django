import { useMemo } from "react";

import {
  autoExtractAggregate,
  autoExtractGroupBy,
  type AggregateBucket,
} from "./aggregate-extract";
import { useDocumentQuery } from "./document-query";
import {
  aggregateFieldName,
  assembleAggregateDocument,
  assembleGroupByDocument,
  groupByFieldName,
} from "./selection";

export type {
  AggregateBucket,
  AggregateFn,
  GroupByResult,
  MeasureMap,
} from "./aggregate-extract";
export { selectMeasure } from "./aggregate-extract";

export interface UseAggregateOptions {
  measureFields?: readonly string[];
  search?: string | null;
  enabled?: boolean;
}

/** One row of ungrouped totals for a model (count plus numeric measures). */
export function useAggregateQuery(
  modelLabel: string,
  options: UseAggregateOptions = {},
): { aggregate: AggregateBucket | null; fetching: boolean; error: Error | null } {
  const { measureFields = [], search, enabled = true } = options;
  const active = enabled && Boolean(modelLabel);
  const measuresKey = measureFields.join(" ");

  const document = useMemo(
    () => assembleAggregateDocument(modelLabel, measureFields),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [modelLabel, measuresKey],
  );
  const variables = useMemo(() => ({ search: search ?? null }), [search]);

  const run = useDocumentQuery(document, variables, active);
  return {
    aggregate: autoExtractAggregate(run.data, aggregateFieldName(modelLabel)),
    fetching: run.fetching,
    error: run.error,
  };
}

/** A group-by spec entry, e.g. `{ field: "STATE" }`. */
export interface GroupBySpecInput {
  field: string;
}

export interface UseGroupByOptions extends UseAggregateOptions {
  groupBy: readonly GroupBySpecInput[];
  keyFields: readonly string[];
}

/** Grouped totals for a model: one bucket per distinct group key. */
export function useResourceGroupBy(
  modelLabel: string,
  options: UseGroupByOptions,
): {
  totalCount: number;
  buckets: readonly AggregateBucket[];
  fetching: boolean;
  error: Error | null;
} {
  const { groupBy, keyFields, measureFields = [], search, enabled = true } = options;
  const active = enabled && Boolean(modelLabel) && groupBy.length > 0;
  const keyFieldsKey = keyFields.join(" ");
  const measuresKey = measureFields.join(" ");

  const document = useMemo(
    () => assembleGroupByDocument(modelLabel, keyFields, measureFields),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [modelLabel, keyFieldsKey, measuresKey],
  );
  const variables = useMemo(
    () => ({ groupBy, search: search ?? null }),
    [groupBy, search],
  );

  const run = useDocumentQuery(document, variables, active);
  const result = autoExtractGroupBy(run.data, groupByFieldName(modelLabel));
  return {
    totalCount: result.totalCount,
    buckets: result.buckets,
    fetching: run.fetching,
    error: run.error,
  };
}
