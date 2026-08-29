import * as React from "react";
import { formatDistanceToNow } from "date-fns";

import { cn } from "../lib/cn";
import { dateFromUnknown } from "../widgets/date-format";

export interface RelativeTimeProps
  extends Omit<React.TimeHTMLAttributes<HTMLTimeElement>, "children" | "dateTime"> {
  value: Date | string | number | null | undefined;
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
  const date = dateFromUnknown(value);
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
