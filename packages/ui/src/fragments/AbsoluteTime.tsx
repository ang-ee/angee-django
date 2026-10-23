import * as React from "react";

import { cn } from "../lib/cn";
import { dateFromUnknown, formatDate, formatDateTime, formatTime } from "../widgets/date-format";

export interface AbsoluteTimeProps
  extends Omit<React.TimeHTMLAttributes<HTMLTimeElement>, "children" | "dateTime"> {
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
  const options = timeZone ? { timeZone } : {};
  const full = formatDateTime(date, options);
  const label = display === "time" ? formatTime(date, options) : display === "date" ? formatDate(date, options) : full;
  return (
    <time
      dateTime={date.toISOString()}
      title={display === "datetime" ? undefined : full}
      className={cn("tabular-nums", className)}
      {...props}
    >
      {label}
    </time>
  );
}
