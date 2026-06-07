import { Link } from "@tanstack/react-router";
import type { ReactElement } from "react";

import { cn } from "../lib/cn";
import {
  useRouteBreadcrumbItems,
  type BreadcrumbItem,
} from "../route-static-data";

export type { BreadcrumbItem } from "../route-static-data";

export interface BreadcrumbProps {
  className?: string;
  /** Controlled trail; bypasses match-derived items. */
  items?: readonly BreadcrumbItem[];
}

export function Breadcrumb({
  className,
  items,
}: BreadcrumbProps): ReactElement {
  if (items) return <BreadcrumbTrail className={className} items={items} />;
  return <MatchedBreadcrumbTrail className={className} />;
}

function MatchedBreadcrumbTrail({
  className,
}: {
  className?: string;
}): ReactElement {
  const items = useRouteBreadcrumbItems();
  return <BreadcrumbTrail className={className} items={items} />;
}

function BreadcrumbTrail({
  className,
  items,
}: {
  className?: string;
  items: readonly BreadcrumbItem[];
}): ReactElement {
  return (
    <nav
      aria-label="Breadcrumb"
      className={cn(
        "area-crumbs z-breadcrumb flex h-crumbs-h min-w-0 items-center gap-1 border-b border-border-subtle bg-sheet px-4 text-13 text-fg-muted",
        className,
      )}
    >
      {items.map((item, index) => {
        const current = index === items.length - 1;
        const key = `${itemKey(item.label)}:${index}`;
        return (
          <span key={key} className="contents">
            {index > 0 ? (
              <span aria-hidden className="shrink-0 text-fg-subtle">
                /
              </span>
            ) : null}
            {item.to && !current ? (
              <Link
                to={item.to}
                className="min-w-0 truncate rounded-sm outline-none hover:text-fg focus-visible:focus-ring"
              >
                {item.label}
              </Link>
            ) : (
              <span
                aria-current={current ? "page" : undefined}
                className="min-w-0 truncate font-medium text-fg"
              >
                {item.label}
              </span>
            )}
          </span>
        );
      })}
    </nav>
  );
}

function itemKey(label: BreadcrumbItem["label"]): string {
  if (typeof label === "string" || typeof label === "number") {
    return String(label);
  }
  return "";
}
