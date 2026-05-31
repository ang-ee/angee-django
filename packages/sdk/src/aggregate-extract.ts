// Pure extractors that turn an aggregate response into the buckets the dashboard
// widgets read. Kept free of React so they unit-test directly.
//
// The aggregate field is count-only: the ungrouped result carries the total
// `count`; a grouped result adds `groups`, each carrying its own `count` and the
// dimension values it was grouped by. Everything on a group except `count` forms
// the bucket key.

/** One aggregate row: an optional group key and a count. */
export interface AggregateBucket {
  key: Record<string, unknown> | null;
  count: number;
}

export interface GroupByResult {
  count: number;
  buckets: readonly AggregateBucket[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function countOf(value: unknown): number {
  return typeof value === "number" ? value : 0;
}

/** A group object becomes a bucket: its `count`, with the rest as the key. */
function toBucket(group: Record<string, unknown>): AggregateBucket {
  const { count, ...key } = group;
  return { key, count: countOf(count) };
}

/** Extract the ungrouped aggregate bucket at `field` (count only), or null. */
export function autoExtractAggregate(
  data: unknown,
  field: string,
): AggregateBucket | null {
  if (!isRecord(data)) return null;
  const node = data[field];
  if (!isRecord(node)) return null;
  return { key: null, count: countOf(node.count) };
}

/** Extract the grouped buckets and the total count at `field`. */
export function autoExtractGroupBy(data: unknown, field: string): GroupByResult {
  const node = isRecord(data) ? data[field] : undefined;
  if (!isRecord(node)) return { count: 0, buckets: [] };
  const groups = Array.isArray(node.groups) ? node.groups : [];
  return {
    count: countOf(node.count),
    buckets: groups.filter(isRecord).map(toBucket),
  };
}
