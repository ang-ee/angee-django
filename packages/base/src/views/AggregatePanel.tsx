import * as React from "react";
import {
  useResourceAggregate,
  useResourceGroupBy,
  type AggregateBucket,
  type GroupByDimension,
} from "@angee/sdk";

import { CountBadge } from "../ui/badge";
import { Spinner } from "../ui/spinner";

/** One grouped dimension plus how to label it in the panel header. */
export interface AggregateDimension extends GroupByDimension {
  /** Heading for the dimension; defaults to the bucket `field`. */
  label?: React.ReactNode;
}

export interface AggregatePanelProps {
  /** Model label, e.g. `"notes.Note"`. */
  model: string;
  /** Group dimensions; the first drives the buckets, the rest are reserved. */
  dimensions: readonly AggregateDimension[];
  /** Title above the rows; defaults to the first dimension's label. */
  title?: React.ReactNode;
  /** Turns a raw group key into a display label. */
  formatKey?: (key: unknown) => React.ReactNode;
  className?: string;
}

/** The group key value a bucket carries for the given dimension. */
function keyValue(bucket: AggregateBucket, field: string): unknown {
  if (!bucket.key) return null;
  if (field in bucket.key) return bucket.key[field];
  const fkKey = `${field}Id`;
  if (fkKey in bucket.key) return bucket.key[fkKey];
  return null;
}

function defaultFormat(key: unknown): React.ReactNode {
  if (key == null) return "—";
  return String(key);
}

/** Count-by-group rows for one model, with a total. */
export function AggregatePanel({
  model,
  dimensions,
  title,
  formatKey = defaultFormat,
  className,
}: AggregatePanelProps): React.ReactElement {
  const grouped = dimensions.length > 0;
  const group = useResourceGroupBy(model, {
    dimensions,
    enabled: grouped,
  });
  const ungrouped = useResourceAggregate(model, { enabled: !grouped });

  const fetching = grouped ? group.fetching : ungrouped.fetching;
  const error = grouped ? group.error : ungrouped.error;
  const total = grouped ? group.count : (ungrouped.aggregate?.count ?? 0);
  const primary = dimensions[0];
  const primaryKey = primary?.key ?? primary?.field;
  const heading = title ?? primary?.label ?? primaryKey ?? "Total";

  // Largest count drives the bar widths so the panel reads as a tiny chart.
  const maxCount = group.buckets.reduce(
    (max, bucket) => Math.max(max, bucket.count),
    0,
  );

  return (
    <div
      className={[
        "flex flex-col gap-2 rounded-md border border-border bg-sheet p-3",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <div className="flex items-center justify-between text-13">
        <span className="font-semibold text-fg">{heading}</span>
        <CountBadge value={total} />
      </div>

      {error ? (
        <p className="text-13 text-danger-text">{error.message}</p>
      ) : fetching ? (
        <div className="flex items-center gap-2 py-2 text-13 text-fg-muted">
          <Spinner size="sm" />
          Loading…
        </div>
      ) : !grouped ? null : group.buckets.length === 0 ? (
        <p className="py-1 text-13 text-fg-muted">No data.</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {group.buckets.map((bucket, index) => {
            const key = primaryKey ? keyValue(bucket, primaryKey) : null;
            const width = maxCount > 0 ? (bucket.count / maxCount) * 100 : 0;
            return (
              <li key={`${String(key)}#${index}`} className="flex flex-col gap-1">
                <div className="flex items-center justify-between text-13 text-fg">
                  <span className="truncate">{formatKey(key)}</span>
                  <span className="tabular-nums text-fg-muted">
                    {bucket.count}
                  </span>
                </div>
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-inset">
                  <div
                    className="h-full rounded-full bg-brand"
                    style={{ width: `${width}%` }}
                  />
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
