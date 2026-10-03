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
  /** `rail` renders on the top bar's dark surface. */
  tone?: "sheet" | "rail";
}

const TRAIL_TONES = {
  sheet: { trail: "text-fg-muted", separator: "text-fg-subtle", link: "hover:text-fg", current: "text-fg" },
  rail: { trail: "text-on-rail-mut", separator: "text-on-rail-mut/60", link: "hover:text-on-rail-hi", current: "text-on-rail-hi" },
} as const;

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
  tone = "sheet",
}: BreadcrumbProps): ReactElement {
  return <BreadcrumbTrail className={className} tone={tone} items={useBreadcrumbItems()} />;
}

/** The displayed trail, led by the selected app, with the record's published label and return link. */
export function useBreadcrumbItems(): readonly BreadcrumbItem[] {
  const t = useUiT();
  const place = useOptionalChromePlace();
  const leafLabel = React.useContext(BreadcrumbLeafLabelContext);
  const collection = React.useContext(BreadcrumbCollectionContext)?.link;
  const items = breadcrumbItemsFromRefine(useRefineBreadcrumb().breadcrumbs, leafLabel, collection);
  // The top bar shows only the app's menus, so the trail names the app.
  const match = place?.match;
  const app: BreadcrumbItem | undefined = match?.trail[0]?.group === "platform"
    ? { label: t("chrome.settings"), to: place?.tree.settingsEntry()?.target }
    : match?.app ? { label: match.app.displayLabel, to: match.app.target } : undefined;
  return app && !items.some((item) => item.label === app.label) ? [app, ...items] : items;
}

function BreadcrumbTrail({
  className,
  tone,
  items,
}: {
  className?: string;
  tone: NonNullable<BreadcrumbProps["tone"]>;
  items: readonly BreadcrumbItem[];
}): ReactElement {
  const t = useUiT();
  const colors = TRAIL_TONES[tone];
  return (
    <nav
      aria-label={t("chrome.breadcrumb")}
      className={cn(
        "flex min-w-0 items-center gap-1 overflow-hidden text-13",
        colors.trail,
        className,
      )}
    >
      {items.map((item, index) => {
        const current = index === items.length - 1;
        const key = `${itemKey(item.label)}:${index}`;
        return (
          <span key={key} className="contents">
            {index > 0 ? (
              <span aria-hidden className={cn("shrink-0", colors.separator)}>
                /
              </span>
            ) : null}
            {item.to && !current ? (
              <Link
                to={item.to}
                href={item.to}
                className={cn("min-w-0 truncate rounded-4 outline-none focus-visible:focus-ring", colors.link)}
              >
                {item.label}
              </Link>
            ) : (
              <span
                aria-current={current ? "page" : undefined}
                className={cn("min-w-0 truncate font-medium", colors.current)}
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
