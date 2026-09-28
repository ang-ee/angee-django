import type { ReactElement, ReactNode } from "react";
import { Link } from "@tanstack/react-router";

import { cn } from "../lib/cn";

export interface AppBrandProps {
  className?: string;
  compact?: boolean;
  mark?: ReactNode;
  name: string;
  to?: string;
}

export function AppBrand({
  className,
  compact = false,
  mark,
  name,
  to = "/",
}: AppBrandProps): ReactElement {
  return (
    <Link
      to={to}
      aria-label={name}
      className={cn(
        "flex h-7 min-w-0 items-center gap-2 rounded-6 px-2 text-sm font-semibold text-on-rail outline-none transition-colors hover:bg-rail-hi hover:text-on-rail-hi focus-visible:focus-ring",
        className,
      )}
    >
      {mark != null ? <span className="grid size-4 shrink-0 place-content-center text-brand [&>svg]:size-4">
        {mark}
      </span> : null}
      <span className={compact ? "sr-only" : "min-w-0 truncate"}>{name}</span>
    </Link>
  );
}
