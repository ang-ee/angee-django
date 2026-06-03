// Pure extractors that turn an aggregate response into the buckets the dashboard
// widgets read. Kept free of React so they unit-test directly.
//
// Aggregate results carry a `count` plus optional measure maps such as
// `sum { amount }`. Grouped results are offset-paginated envelopes with
// `results`; older schemas exposed grouped rows as `groups` beneath the
// aggregate field, so the extractor accepts both shapes while the document
// builder emits the newer one.

export const AGGREGATE_MEASURE_OPERATORS = [
  "sum",
  "avg",
  "min",
  "max",
] as const;

export type AggregateMeasureOperator =
  (typeof AGGREGATE_MEASURE_OPERATORS)[number];

export interface AggregateMeasure {
  op: AggregateMeasureOperator;
  field: string;
}

export type AggregateMeasureValues = Record<string, unknown>;

/** One aggregate row: an optional group key, count, and requested measures. */
export interface AggregateBucket {
  key: Record<string, unknown> | null;
  count: number;
  filter?: Record<string, unknown> | null;
  sum?: AggregateMeasureValues;
  avg?: AggregateMeasureValues;
  min?: AggregateMeasureValues;
  max?: AggregateMeasureValues;
}

export interface GroupByResult {
  /** Sum of row counts in the returned buckets. */
  count: number;
  /** Total number of groups in the backend result, independent of pagination. */
  totalCount: number;
  buckets: readonly AggregateBucket[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function countOf(value: unknown): number {
  return typeof value === "number" ? value : 0;
}

function measureValuesOf(
  value: unknown,
): AggregateMeasureValues | undefined {
  return isRecord(value) ? value : undefined;
}

function extractMeasures(
  source: Record<string, unknown>,
): Partial<Record<AggregateMeasureOperator, AggregateMeasureValues>> {
  const measures: Partial<
    Record<AggregateMeasureOperator, AggregateMeasureValues>
  > = {};
  for (const op of AGGREGATE_MEASURE_OPERATORS) {
    const values = measureValuesOf(source[op]);
    if (values !== undefined) measures[op] = values;
  }
  return measures;
}

/** A legacy group object becomes a bucket: its `count`, with the rest as key. */
function toBucket(group: Record<string, unknown>): AggregateBucket {
  const { count, ...key } = group;
  return { key, count: countOf(count) };
}

/** A grouped-result row carries its key under `key` and its row count. */
function toGroupedResultBucket(group: Record<string, unknown>): AggregateBucket {
  const key = isRecord(group.key) ? group.key : {};
  const bucket: AggregateBucket = {
    key,
    count: countOf(group.count),
    ...extractMeasures(group),
  };
  if (isRecord(group.filter)) bucket.filter = group.filter;
  return bucket;
}

/** Extract the ungrouped aggregate bucket at `field`, or null. */
export function autoExtractAggregate(
  data: unknown,
  field: string,
): AggregateBucket | null {
  if (!isRecord(data)) return null;
  const node = data[field];
  if (!isRecord(node)) return null;
  return { key: null, count: countOf(node.count), ...extractMeasures(node) };
}

/** Extract the grouped buckets and the total count at `field`. */
export function autoExtractGroupBy(data: unknown, field: string): GroupByResult {
  const node = isRecord(data) ? data[field] : undefined;
  if (!isRecord(node)) return { count: 0, totalCount: 0, buckets: [] };
  if (Array.isArray(node.results)) {
    const buckets = node.results.filter(isRecord).map(toGroupedResultBucket);
    return {
      count: buckets.reduce((total, bucket) => total + bucket.count, 0),
      totalCount: countOf(node.totalCount),
      buckets,
    };
  }
  const groups = Array.isArray(node.groups) ? node.groups : [];
  const buckets = groups.filter(isRecord).map(toBucket);
  return {
    count: countOf(node.count),
    totalCount: buckets.length,
    buckets,
  };
}
