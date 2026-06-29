import * as React from "react";
import {
  useModelMetadata,
} from "@angee/resources";
import {
  useAngeeAggregate,
  useAngeeGroupBy,
} from "../data/hooks";
import type { AggregateBucket } from "@angee/refine";

import { useBaseT } from "../i18n";
import { cn } from "../lib/cn";
import { CountBadge } from "../ui/badge";
import { Skeleton, SkeletonStatus } from "../ui/skeleton";
import { textRoleVariants } from "../ui/text";
import {
  hasuraGroupDimension,
  type GroupByDimension,
} from "./ListInternals";

/** One grouped dimension plus how to label it in the panel header. */
export interface AggregateDimension extends GroupByDimension {
  /** Heading for the dimension; defaults to the bucket `field`. */
  label?: React.ReactNode;
}

export interface AggregatePanelProps {
  /** Resource label, e.g. `"notes.Note"`. */
  resource: string;
  /** Group dimensions; the first drives the buckets, the rest are reserved. */
  dimensions: readonly AggregateDimension[];
  /** Title above the rows; defaults to the first dimension's label. */
  title?: React.ReactNode;
  /** Turns a raw group key into a display label. */
  formatKey?: (key: unknown) => React.ReactNode;
  className?: string;
}

function defaultFormat(key: unknown): React.ReactNode {
  if (key == null) return "—";
  return String(key);
}

/** Count-by-group rows for one model, with a total. */
export function AggregatePanel({
  resource,
  dimensions,
  title,
  formatKey = defaultFormat,
  className,
}: AggregatePanelProps): React.ReactElement {
  const t = useBaseT();
  const grouped = dimensions.length > 0;
  const metadata = useModelMetadata(resource);
  const dataResource = requireDataResource(resource, metadata);
  const groupDimensions = React.useMemo(
    () => dimensions.map(hasuraGroupDimension),
    [dimensions],
  );
  const group = useAngeeGroupBy(dataResource, {
    dimensions: groupDimensions,
    enabled: grouped,
  });
  const ungrouped = useAngeeAggregate(dataResource, { enabled: !grouped });

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
        "flex flex-col gap-2 rounded-6 border border-border bg-sheet p-3",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <div className="flex items-center justify-between text-13">
        <span className="font-semibold text-fg">{heading}</span>
        {fetching ? (
          <Skeleton shape="text" size="sm" className="w-8" />
        ) : (
          <CountBadge value={total} />
        )}
      </div>

      {error ? (
        <p className="text-13 text-danger-text">{error.message}</p>
      ) : fetching ? (
        <AggregateSkeleton grouped={grouped} loadingLabel={t("aggregate.loading")} />
      ) : !grouped ? null : group.buckets.length === 0 ? (
        <p className={cn(textRoleVariants({ role: "meta" }), "py-1")}>{t("aggregate.noData")}</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {group.buckets.map((bucket, index) => {
            const key = primary ? bucketKey(bucket, primary) : null;
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

function bucketKey(
  bucket: AggregateBucket,
  dimension: GroupByDimension,
): unknown {
  return bucket.key?.[dimension.key ?? dimension.field] ?? null;
}

function requireDataResource(
  resourceId: string,
  metadata: ReturnType<typeof useModelMetadata>,
): NonNullable<NonNullable<ReturnType<typeof useModelMetadata>>["resource"]> {
  const dataResource = metadata?.resource;
  if (!dataResource) {
    throw new Error(`Resource "${resourceId}" has no data resource metadata.`);
  }
  return dataResource;
}

function AggregateSkeleton({
  grouped,
  loadingLabel,
}: {
  grouped: boolean;
  loadingLabel: React.ReactNode;
}): React.ReactElement {
  if (!grouped) {
    return (
      <SkeletonStatus label={loadingLabel} className="my-1">
        <Skeleton shape="text" size="sm" className="w-2/3" />
      </SkeletonStatus>
    );
  }

  return (
    <SkeletonStatus label={loadingLabel}>
      <ul className="flex flex-col gap-2" aria-hidden="true">
        {Array.from({ length: 4 }, (_, index) => (
          <li key={index} className="flex flex-col gap-1">
            <div className="flex items-center justify-between gap-3">
              <Skeleton
                shape="text"
                size="sm"
                className={index % 2 === 0 ? "w-28" : "w-20"}
              />
              <Skeleton shape="text" size="sm" className="w-7" />
            </div>
            <Skeleton className={index % 2 === 0 ? "h-1.5 w-full" : "h-1.5 w-2/3"} />
          </li>
        ))}
      </ul>
    </SkeletonStatus>
  );
}
