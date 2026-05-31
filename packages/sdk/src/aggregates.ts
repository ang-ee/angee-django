import { useMemo } from "react";

import {
  autoExtractAggregate,
  autoExtractGroupBy,
  type AggregateBucket,
} from "./aggregate-extract";
import { useDocumentQuery } from "./document-query";
import { useStableArray } from "./stable-deps";
import { aggregateFieldName, assembleAggregateDocument } from "./selection";

export type { AggregateBucket, GroupByResult } from "./aggregate-extract";

// Stable empty variables for the ungrouped query, so the hook does not re-run on
// every render.
const NO_VARIABLES: Record<string, unknown> = {};

export interface UseAggregateOptions {
  enabled?: boolean;
}

/** The ungrouped total for a model. */
export function useAggregateQuery(
  modelLabel: string,
  options: UseAggregateOptions = {},
): { aggregate: AggregateBucket | null; fetching: boolean; error: Error | null } {
  const { enabled = true } = options;
  const active = enabled && Boolean(modelLabel);

  const document = useMemo(
    () => assembleAggregateDocument(modelLabel),
    [modelLabel],
  );
  const run = useDocumentQuery(document, NO_VARIABLES, active);
  return {
    aggregate: autoExtractAggregate(run.data, aggregateFieldName(modelLabel)),
    fetching: run.fetching,
    error: run.error,
  };
}

/**
 * A group-by dimension: the enum value to group on (`by`, e.g. `"STATUS"`) and
 * the field that carries its value in each bucket (`field`, e.g. `"status"`).
 */
export interface GroupByDimension {
  by: string;
  field: string;
}

export interface UseGroupByOptions extends UseAggregateOptions {
  dimensions: readonly GroupByDimension[];
}

/** Grouped totals for a model: one bucket per distinct group key. */
export function useResourceGroupBy(
  modelLabel: string,
  options: UseGroupByOptions,
): {
  count: number;
  buckets: readonly AggregateBucket[];
  fetching: boolean;
  error: Error | null;
} {
  const { dimensions, enabled = true } = options;
  const active = enabled && Boolean(modelLabel) && dimensions.length > 0;
  const fields = useStableArray(dimensions.map((dimension) => dimension.field));
  const groupBy = useStableArray(dimensions.map((dimension) => dimension.by));

  const document = useMemo(
    () => assembleAggregateDocument(modelLabel, fields),
    [modelLabel, fields],
  );
  const variables = useMemo(() => ({ groupBy }), [groupBy]);

  const run = useDocumentQuery(document, variables, active);
  const result = autoExtractGroupBy(run.data, aggregateFieldName(modelLabel));
  return {
    count: result.count,
    buckets: result.buckets,
    fetching: run.fetching,
    error: run.error,
  };
}
