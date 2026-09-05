import { Link } from "@tanstack/react-router";
import {
  useBreadcrumb as useRefineBreadcrumb,
  type BreadcrumbsType,
} from "@refinedev/core";
import * as React from "react";
import type { ReactElement } from "react";

import { useUiT } from "../i18n";
import { cn } from "../lib/cn";

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
export function useBreadcrumbLeafLabel(label: string | null | undefined): void {
  const setLeafLabel = React.useContext(BreadcrumbLeafLabelSetterContext);
  React.useEffect(() => {
    if (!setLeafLabel) return;
    const next = label?.trim() ? label : null;
    setLeafLabel(next);
    return () => setLeafLabel(null);
  }, [label, setLeafLabel]);
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
  const leafLabel = React.useContext(BreadcrumbLeafLabelContext);
  const collection = React.useContext(BreadcrumbCollectionContext)?.link;
  const items = breadcrumbItemsFromRefine(
    useRefineBreadcrumb().breadcrumbs,
    leafLabel,
    collection,
  );
  return <BreadcrumbTrail className={className} items={items} />;
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
        "flex min-w-0 items-center gap-1 overflow-hidden text-13 text-on-rail-mut",
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
                className="min-w-0 truncate rounded-4 outline-none hover:text-on-rail-hi focus-visible:focus-ring"
              >
                {item.label}
              </Link>
            ) : (
              <span
                aria-current={current ? "page" : undefined}
                className="min-w-0 truncate font-medium text-on-rail-hi"
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
  return breadcrumbs.map((item) => ({
    label:
      leafLabel && item === breadcrumbs.at(-1) ? leafLabel : item.label,
    ...(item.href ? { to: item.href === collection?.to ? collection.href : item.href } : {}),
  }));
}
