import { Link } from "@tanstack/react-router";
import {
  useBreadcrumb as useRefineBreadcrumb,
  type BreadcrumbsType,
} from "@refinedev/core";
import * as React from "react";
import type { ReactElement } from "react";

import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { useOptionalChromePlace } from "./refine-menu";

export interface BreadcrumbItem {
  label: string;
  to?: string;
}

export interface BreadcrumbProps {
  className?: string;
}

const BreadcrumbLeafLabelContext = React.createContext<string | null>(null);
const BreadcrumbLeafLabelSetterContext = React.createContext<
  ((label: string | null) => void) | null
>(null);

interface BreadcrumbCollectionLink {
  to: string;
  href: string;
}

const BreadcrumbCollectionContext = React.createContext<{
  link: BreadcrumbCollectionLink | null;
  setLink: React.Dispatch<React.SetStateAction<BreadcrumbCollectionLink | null>>;
} | null>(null);

export function BreadcrumbLabelProvider({
  children,
}: {
  children: React.ReactNode;
}): ReactElement {
  const [leafLabel, setLeafLabel] = React.useState<string | null>(null);
  const [link, setLink] = React.useState<BreadcrumbCollectionLink | null>(null);
  const collection = React.useMemo(() => ({ link, setLink }), [link]);
  return (
    <BreadcrumbCollectionContext.Provider value={collection}>
      <BreadcrumbLeafLabelSetterContext.Provider value={setLeafLabel}>
        <BreadcrumbLeafLabelContext.Provider value={leafLabel}>
          {children}
        </BreadcrumbLeafLabelContext.Provider>
      </BreadcrumbLeafLabelSetterContext.Provider>
    </BreadcrumbCollectionContext.Provider>
  );
}

/** Let a route page replace the generic current crumb with its record label. */
export function useBreadcrumbLeafLabel(
  label: string | null | undefined,
  enabled = true,
): void {
  const setLeafLabel = React.useContext(BreadcrumbLeafLabelSetterContext);
  React.useEffect(() => {
    if (!setLeafLabel || !enabled) return;
    const next = label?.trim() ? label : null;
    setLeafLabel(next);
    return () => setLeafLabel(null);
  }, [enabled, label, setLeafLabel]);
}

/** Publish the routed collection owner's return URL for its matching crumb. */
export function useBreadcrumbCollectionLink(to: string, href: string | null): void {
  const setLink = React.useContext(BreadcrumbCollectionContext)?.setLink;
  React.useEffect(() => {
    if (!setLink) return;
    setLink(href === null ? null : { to, href });
    return () => setLink(null);
  }, [to, href, setLink]);
}

export function Breadcrumb({
  className,
}: BreadcrumbProps): ReactElement {
  return <BreadcrumbTrail className={className} items={useNestedBreadcrumbItems()} />;
}

/** The full trail, including the record's published label and return link. */
export function useBreadcrumbItems(): readonly BreadcrumbItem[] {
  const leafLabel = React.useContext(BreadcrumbLeafLabelContext);
  const collection = React.useContext(BreadcrumbCollectionContext)?.link;
  return breadcrumbItemsFromRefine(useRefineBreadcrumb().breadcrumbs, leafLabel, collection);
}

/**
 * The trail worth showing below the top bar. It is empty on a menu destination,
 * because the top bar already names the app and its menus. Deeper (a record, or
 * anything else the menus do not name), it is the current menu page followed by
 * the deeper crumbs.
 */
export function useNestedBreadcrumbItems(): readonly BreadcrumbItem[] {
  const items = useBreadcrumbItems();
  const match = useOptionalChromePlace()?.match;
  if (!match) return items;
  const menuLabels = new Set(match.trail.map((node) => node.displayLabel));
  const deeper = items.filter((item) => !menuLabels.has(item.label));
  if (!deeper.length) return [];
  const page = match.item.id === match.app?.id ? undefined : match.item;
  // Prefer the trail's own crumb for the page: it carries the collection's return link.
  const pageItem = page && (items.find((item) => item.label === page.displayLabel) ?? { label: page.displayLabel, to: page.target });
  return pageItem ? [pageItem, ...deeper] : deeper;
}

function BreadcrumbTrail({
  className,
  items,
}: {
  className?: string;
  items: readonly BreadcrumbItem[];
}): ReactElement {
  const t = useUiT();
  return (
    <nav
      aria-label={t("chrome.breadcrumb")}
      className={cn(
        "flex min-w-0 items-center gap-1 overflow-hidden text-13 text-fg-muted",
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
                href={item.to}
                className="min-w-0 truncate rounded-4 outline-none hover:text-fg focus-visible:focus-ring"
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
  leafLabel?: string | null,
  collection?: BreadcrumbCollectionLink | null,
): readonly BreadcrumbItem[] {
  const items = breadcrumbs.map((item) => ({
    label:
      leafLabel && item === breadcrumbs.at(-1) ? leafLabel : item.label,
    ...(item.href ? { to: item.href === collection?.to ? collection.href : item.href } : {}),
  }));

  // Menu grouping may repeat the same human label and destination at adjacent
  // levels (for example Integrations / Integrations / Integrations). Keep the
  // deepest owner without collapsing equally named, distinct destinations.
  return items.filter((item, index) => {
    const next = items[index + 1];
    return item.label !== next?.label || item.to !== next.to;
  });
}
