import type { ReactElement, ReactNode } from "react";
import { Link } from "@tanstack/react-router";

import { cn } from "../lib/cn";
import { AngeeMark } from "./AngeeMark";

export interface AppBrandProps {
  className?: string;
  mark?: ReactNode;
  name?: ReactNode;
  to?: string;
}

export function AppBrand({
  className,
  mark,
  name = "Angee",
  to = "/",
}: AppBrandProps): ReactElement {
  const label = typeof name === "string" ? name : "Angee";
  return (
    <Link
      to={to}
      aria-label={label}
      className={cn(
        "flex h-7 min-w-0 items-center gap-2 rounded-6 px-2 text-sm font-semibold text-on-rail outline-none transition-colors hover:bg-rail-hi hover:text-on-rail-hi focus-visible:focus-ring",
        className,
      )}
    >
      <span className="grid size-4 shrink-0 place-content-center text-brand [&>svg]:size-4">
        {mark ?? <AngeeMark size={16} aria-hidden="true" />}
      </span>
      <span className="min-w-0 truncate">{name}</span>
    </Link>
  );
}
