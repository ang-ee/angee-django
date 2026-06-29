import * as React from "react";
import { formatDistanceToNow } from "date-fns";

import { cn } from "../lib/cn";

export interface RelativeTimeProps
  extends Omit<React.TimeHTMLAttributes<HTMLTimeElement>, "children" | "dateTime"> {
  value: Date | string | null | undefined;
  addSuffix?: boolean;
  fallback?: React.ReactNode;
}

export function RelativeTime({
  addSuffix = true,
  className,
  fallback = null,
  value,
  ...props
}: RelativeTimeProps): React.ReactElement | null {
  const date = parseTimeValue(value);
  if (!date) return fallback ? <>{fallback}</> : null;

  return (
    <time
      dateTime={date.toISOString()}
      className={cn("tabular-nums", className)}
      {...props}
    >
      {formatDistanceToNow(date, { addSuffix })}
    </time>
  );
}

function parseTimeValue(value: Date | string | null | undefined): Date | null {
  if (!value) return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}
