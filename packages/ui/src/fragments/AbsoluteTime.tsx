import * as React from "react";

import { cn } from "../lib/cn";
import { dateFromUnknown, formatDate, formatDateTime, formatTime, isStorageDate } from "../widgets/date-format";

export interface AbsoluteTimeProps
  extends Omit<React.TimeHTMLAttributes<HTMLTimeElement>, "children" | "dateTime"> {
  /** An instant, or a bare calendar day (`yyyy-MM-dd`), which reads as that date in every zone, with no time. */
  value: Date | string | number | null | undefined;
  /** `time` (default) reads the clock time, `date` the day, `datetime` both; the full stamp is the tooltip. */
  display?: "time" | "date" | "datetime";
  /** Calendar time zone for the label. */
  timeZone?: string;
  fallback?: React.ReactNode;
}

/**
 * The absolute time of a row or a fact: the time rule keeps `RelativeTime` for live
 * headers and renders every list, row and facts line absolutely. The label is the
 * chosen part; the full date and time stay as the tooltip.
 * @kind fragment
 */
export function AbsoluteTime({
  className,
  display = "time",
  fallback = null,
  timeZone,
  value,
  ...props
}: AbsoluteTimeProps): React.ReactElement | null {
  const date = dateFromUnknown(value);
  if (!date) return fallback ? <>{fallback}</> : null;
  // A day has no clock time and no zone: its label and its full stamp are both the date.
  const day = isStorageDate(value);
  const options = timeZone && !day ? { timeZone } : {};
  const full = day ? formatDate(date, { density: "full" }) : formatDateTime(date, options);
  const label = display === "datetime" ? full : display === "time" && !day ? formatTime(date, options) : formatDate(date, options);
  return (
    <time
      dateTime={day ? value : date.toISOString()}
      title={display === "datetime" ? undefined : full}
      className={cn("tabular-nums", className)}
      {...props}
    >
      {label}
    </time>
  );
}
