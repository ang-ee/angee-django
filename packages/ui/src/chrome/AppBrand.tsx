import type { ComponentPropsWithRef, ReactElement, ReactNode } from "react";
import { Link } from "@tanstack/react-router";

import { useHrefLinkOptions } from "../lib/in-app-link";
import { cn } from "../lib/cn";

export interface AppBrandProps extends Omit<ComponentPropsWithRef<"a">, "children"> {
  compact?: boolean;
  mark?: ReactNode;
  name: string;
  to?: string;
}

export function AppBrand({
  className,
  compact = false,
  href,
  mark,
  name,
  to = "/",
  ...props
}: AppBrandProps): ReactElement {
  const hrefOptions = useHrefLinkOptions(href ?? to);
  return (
    <Link
      {...hrefOptions}
      aria-label={name}
      className={cn(
        "flex h-7 min-w-0 items-center gap-2 rounded-6 px-2 text-sm font-semibold text-on-rail outline-none transition-colors hover:bg-rail-hi hover:text-on-rail-hi focus-visible:focus-ring",
        className,
      )}
      {...props}
    >
      {mark != null ? <span className="grid size-4 shrink-0 place-content-center text-brand [&>svg]:size-4">
        {mark}
      </span> : null}
      <span className={compact ? "sr-only" : "min-w-0 truncate"}>{name}</span>
    </Link>
  );
}
