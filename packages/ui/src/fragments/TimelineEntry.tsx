import * as React from "react";

import { cn } from "../lib/cn";
import { textRoleVariants } from "../ui/text";
import { RelativeTime } from "./RelativeTime";

export interface TimelineEntryProps
  extends Omit<React.LiHTMLAttributes<HTMLLIElement>, "title"> {
  title: React.ReactNode;
  timestamp: Date | string | null | undefined;
  body?: unknown;
  emptyBody?: React.ReactNode;
}

export function TimelineEntry({
  body,
  className,
  emptyBody = "No snapshot.",
  timestamp,
  title,
  ...props
}: TimelineEntryProps): React.ReactElement {
  const text = excerpt(body);

  return (
    <li
      className={cn(
        "rounded-6 border border-border-subtle bg-sheet-2 p-3",
        className,
      )}
      {...props}
    >
      <div className="flex min-w-0 items-start justify-between gap-2">
        <p className="truncate text-13 font-semibold text-fg">{title}</p>
        <RelativeTime
          value={timestamp}
          className={cn(textRoleVariants({ role: "caption" }), "shrink-0")}
        />
      </div>
      <p className={cn(textRoleVariants({ role: "description" }), "mt-2 line-clamp-3")}>
        {text || emptyBody}
      </p>
    </li>
  );
}

function excerpt(value: unknown): string {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  return text.length > 160 ? `${text.slice(0, 157)}...` : text;
}
