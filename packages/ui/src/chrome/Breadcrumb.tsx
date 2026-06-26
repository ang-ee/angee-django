import { Link } from "@tanstack/react-router";
import {
  useBreadcrumb as useRefineBreadcrumb,
  type BreadcrumbsType,
} from "@refinedev/core";
import type { ReactElement } from "react";

import { useBaseT } from "../i18n";
import { cn } from "../lib/cn";

export interface BreadcrumbItem {
  label: string;
  to?: string;
}

export interface BreadcrumbProps {
  className?: string;
}

export function Breadcrumb({
  className,
}: BreadcrumbProps): ReactElement {
  const items = breadcrumbItemsFromRefine(useRefineBreadcrumb().breadcrumbs);
  return <BreadcrumbTrail className={className} items={items} />;
}

function BreadcrumbTrail({
  className,
  items,
}: {
  className?: string;
  items: readonly BreadcrumbItem[];
}): ReactElement {
  const t = useBaseT();
  return (
    <nav
      aria-label={t("chrome.breadcrumb")}
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
  return label;
}

function breadcrumbItemsFromRefine(
  breadcrumbs: readonly BreadcrumbsType[],
): readonly BreadcrumbItem[] {
  return breadcrumbs.map((item) => ({
    label: item.label,
    ...(item.href ? { to: item.href } : {}),
  }));
}
