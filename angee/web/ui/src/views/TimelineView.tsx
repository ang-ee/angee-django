import { useMemo, type ReactElement, type ReactNode } from "react";
import { format } from "date-fns";
import type { Row } from "@angee/metadata";

import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { SectionEyebrow } from "../ui/section-eyebrow";
import { TimelineEntry } from "../fragments/TimelineEntry";
import { dateFromUnknown } from "../widgets/date-format";
import { ListEmpty } from "./resource-view-list-body";
import type { ListEmptyContent } from "./resource-view-types";

/**
 * The chronological View — rows bucketed by day (newest first), each rendered
 * as a `TimelineEntry` (the same entry the revisions aside uses). Frameless: it
 * composes inside a `ResourceList` or stands alone over a row array. `renderEntry`
 * overrides the entry body for richer rows (e.g. an avatar).
 */
export interface TimelineViewProps<TRow extends Row = Row> {
  rows?: readonly TRow[];
  /** Field holding an ISO datetime string (the bucket + entry time). */
  dateField: keyof TRow & string;
  /** Field holding the entry title (e.g. the actor or summary). */
  titleField?: keyof TRow & string;
  /** Field holding the entry body. */
  bodyField?: keyof TRow & string;
  /** Field holding the stable row id. */
  rowKey?: keyof TRow & string;
  /** Override the entry body. */
  renderEntry?: (row: TRow) => ReactNode;
  /** Empty-state content shown when there are no dated rows. */
  emptyContent?: ListEmptyContent;
  className?: string;
}

interface DayGroup<TRow> {
  key: string;
  date: Date;
  rows: TRow[];
}

export function TimelineView<TRow extends Row = Row>({
  rows = [],
  dateField,
  titleField,
  bodyField,
  rowKey = "id" as keyof TRow & string,
  renderEntry,
  emptyContent,
  className,
}: TimelineViewProps<TRow>): ReactElement {
  const t = useUiT();
  const resolvedEmptyContent = emptyContent ?? t("list.empty");
  const groups = useMemo<DayGroup<TRow>[]>(() => {
    const dated = rows
      .map((row) => ({ row, date: dateFromUnknown(row[dateField]) }))
      .filter((entry): entry is { row: TRow; date: Date } => entry.date !== null)
      .sort((a, b) => b.date.getTime() - a.date.getTime());
    const buckets = new Map<string, DayGroup<TRow>>();
    for (const { row, date } of dated) {
      const key = format(date, "yyyy-MM-dd");
      const bucket = buckets.get(key);
      if (bucket) bucket.rows.push(row);
      else buckets.set(key, { key, date, rows: [row] });
    }
    return [...buckets.values()];
  }, [rows, dateField]);

  return (
    <div className={cn("flex-1 overflow-y-auto bg-canvas p-6", className)}>
      {groups.length === 0 ? (
        <ListEmpty>{resolvedEmptyContent}</ListEmpty>
      ) : (
      <ol className="mx-auto flex max-w-3xl flex-col gap-6">
        {groups.map((group) => (
          <li key={group.key} className="space-y-3">
            <SectionEyebrow as="h3" tracking="wider" className="sticky top-0 bg-canvas py-1">
              {format(group.date, "EEEE, MMMM d, yyyy")}
            </SectionEyebrow>
            <ol className="flex flex-col gap-2">
              {group.rows.map((row, index) => {
                const id = String(row[rowKey] ?? `entry-${index}`);
                return renderEntry ? (
                  <li key={id} className="rounded-6 border border-border-subtle bg-sheet-2 p-3">
                    {renderEntry(row)}
                  </li>
                ) : (
                  <TimelineEntry
                    key={id}
                    title={titleField ? String(row[titleField] ?? "") : ""}
                    timestamp={dateFromUnknown(row[dateField])}
                    body={bodyField ? row[bodyField] : undefined}
                  />
                );
              })}
            </ol>
          </li>
        ))}
      </ol>
      )}
    </div>
  );
}
