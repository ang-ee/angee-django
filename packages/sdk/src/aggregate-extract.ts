// Pure extractors that turn an aggregate / group-by response into the buckets
// the dashboard widgets read. Kept free of React so they unit-test directly.

export type AggregateFn = "sum" | "avg" | "min" | "max";

/** A measure operator's per-field values as they arrive on the wire. */
export type MeasureMap = Record<string, unknown>;

/** One aggregate row: an optional group key, a count, and per-operator measures. */
export interface AggregateBucket {
  key: Record<string, unknown> | null;
  count: number;
  measures: Partial<Record<AggregateFn, MeasureMap>>;
}

export interface GroupByResult {
  totalCount: number;
  buckets: readonly AggregateBucket[];
}

const MEASURE_OPERATORS: readonly AggregateFn[] = ["sum", "avg", "min", "max"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function toBucket(node: Record<string, unknown>): AggregateBucket {
  const measures: Partial<Record<AggregateFn, MeasureMap>> = {};
  for (const op of MEASURE_OPERATORS) {
    const value = node[op];
    if (isRecord(value)) measures[op] = value;
  }
  return {
    key: isRecord(node.key) ? node.key : null,
    count: typeof node.count === "number" ? node.count : 0,
    measures,
  };
}

/** Extract the single aggregate bucket at `field`, or null when absent. */
export function autoExtractAggregate(
  data: unknown,
  field: string,
): AggregateBucket | null {
  if (!isRecord(data)) return null;
  const node = data[field];
  return isRecord(node) ? toBucket(node) : null;
}

/** Extract the grouped buckets and total at `field`, defaulting to empty. */
export function autoExtractGroupBy(data: unknown, field: string): GroupByResult {
  const node = isRecord(data) ? data[field] : undefined;
  if (!isRecord(node)) return { totalCount: 0, buckets: [] };
  const results = Array.isArray(node.results) ? node.results : [];
  return {
    totalCount: typeof node.totalCount === "number" ? node.totalCount : 0,
    buckets: results.filter(isRecord).map(toBucket),
  };
}

/** Read one measure value from a bucket by operator and field name. */
export function selectMeasure(
  bucket: AggregateBucket | null | undefined,
  fn: AggregateFn,
  field: string,
): number | string | null {
  const value = bucket?.measures[fn]?.[field];
  return typeof value === "number" || typeof value === "string" ? value : null;
}
