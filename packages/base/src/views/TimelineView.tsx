import { useMemo, type ReactElement, type ReactNode } from "react";
import { format, isValid, parseISO } from "date-fns";
import type { Row } from "@angee/sdk";

import { cn } from "../lib/cn";
import { TimelineEntry } from "../fragments/TimelineEntry";

/**
 * The chronological View — rows bucketed by day (newest first), each rendered
 * as a `TimelineEntry` (the same entry the revisions aside uses). Frameless: it
 * composes inside a `DataPage` or stands alone over a row array. `renderEntry`
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
  className,
}: TimelineViewProps<TRow>): ReactElement {
  const groups = useMemo<DayGroup<TRow>[]>(() => {
    const dated = rows
      .map((row) => ({ row, date: parseDate(row[dateField]) }))
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
      <ol className="mx-auto flex max-w-3xl flex-col gap-6">
        {groups.map((group) => (
          <li key={group.key} className="space-y-3">
            <h3 className="sticky top-0 bg-canvas py-1 text-2xs font-semibold uppercase tracking-wider text-fg-muted">
              {format(group.date, "EEEE, MMMM d, yyyy")}
            </h3>
            <ol className="flex flex-col gap-2">
              {group.rows.map((row, index) => {
                const id = String(row[rowKey] ?? `entry-${index}`);
                return renderEntry ? (
                  <li key={id} className="rounded-md border border-border-subtle bg-sheet-2 p-3">
                    {renderEntry(row)}
                  </li>
                ) : (
                  <TimelineEntry
                    key={id}
                    title={titleField ? String(row[titleField] ?? "") : ""}
                    timestamp={parseDate(row[dateField])}
                    body={bodyField ? row[bodyField] : undefined}
                  />
                );
              })}
            </ol>
          </li>
        ))}
      </ol>
    </div>
  );
}

function parseDate(value: unknown): Date | null {
  if (value instanceof Date) return isValid(value) ? value : null;
  if (typeof value !== "string") return null;
  const date = parseISO(value);
  return isValid(date) ? date : null;
}
