import { useRouterState } from "@tanstack/react-router";
import {
  useBreadcrumb as useRefineBreadcrumb,
  type BreadcrumbsType,
} from "@refinedev/core";
import * as React from "react";
import type { ReactElement } from "react";

import { useUiT } from "../i18n";
import { InAppLinkProvider, useInAppNavigator, type InAppNavigator } from "../lib/in-app-link";
import { cn } from "../lib/cn";
import { TextLink } from "../ui/text-link";
import { useOptionalChromePlace } from "./refine-menu";

export interface BreadcrumbItem {
  label: string;
  href?: string;
}

declare module "@tanstack/react-router" {
  interface HistoryState {
    trail?: readonly BreadcrumbItem[];
  }
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
  const location = useRouterState({ select: (state) => state.location });
  const menuLabels = new Set(match?.trail.map((node) => node.displayLabel));
  const deeper = match ? items.filter((item) => !menuLabels.has(item.label)) : items;
  // A collection/menu destination never shows a strip, even if reached from a record.
  if (!deeper.length) return [];
  const current = deeper.map((item, index) => index === deeper.length - 1 ? { ...item, href: location.href } : item);
  const history = location.state.trail ?? [];
  if (history.length) {
    // The previous form supplies the context; the destination's collection is not another step.
    const tail = match ? current : current.slice(-1);
    return dedupeBreadcrumbItems([...history, ...tail]);
  }
  const page = match?.item.id === match?.app?.id ? undefined : match?.item;
  const pageItem = page && (items.find((item) => item.label === page.displayLabel) ?? { label: page.displayLabel, href: page.target });
  return pageItem ? [pageItem, ...current] : current;
}

/** Only console content carries the current form's breadcrumb context to a followed link. */
export function BreadcrumbContentLinks({ children, trail }: {
  children: React.ReactNode;
  trail: readonly BreadcrumbItem[];
}): ReactElement {
  const navigate = useInAppNavigator();
  const follow: InAppNavigator = (href, options) => navigate?.(href, {
    ...options, state: { ...options?.state, ...(trail.length ? { trail } : {}) },
  });
  return navigate ? <InAppLinkProvider navigate={follow}>{children}</InAppLinkProvider> : <>{children}</>;
}

function BreadcrumbTrail({
  className,
  items,
}: {
  className?: string;
  items: readonly BreadcrumbItem[];
}): ReactElement {
  const t = useUiT();
  const navigate = useInAppNavigator();
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
        const key = `${item.label}:${index}`;
        return (
          <span key={key} className="contents">
            {index > 0 ? (
              <span aria-hidden className="shrink-0 text-fg-subtle">
                /
              </span>
            ) : null}
            {item.href && !current ? (
              <BreadcrumbLink
                href={item.href}
                navigate={navigate}
                trailPrefix={items.slice(0, index)}
                className="min-w-0 truncate rounded-4 outline-none hover:text-fg focus-visible:focus-ring"
              >
                {item.label}
              </BreadcrumbLink>
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

function BreadcrumbLink({ href, navigate, trailPrefix, ...props }: React.ComponentProps<typeof TextLink> & {
  href: string;
  navigate: InAppNavigator | undefined;
  trailPrefix: readonly BreadcrumbItem[];
}): ReactElement {
  const follow: InAppNavigator = (target) => navigate?.(target, { state: { trail: trailPrefix } });
  const link = <TextLink href={href} variant="muted" {...props} />;
  return navigate ? <InAppLinkProvider navigate={follow}>{link}</InAppLinkProvider> : link;
}

function breadcrumbItemsFromRefine(
  breadcrumbs: readonly BreadcrumbsType[],
  leafLabel?: string | null,
  collection?: BreadcrumbCollectionLink | null,
): readonly BreadcrumbItem[] {
  const items = breadcrumbs.map((item) => ({
    label:
      leafLabel && item === breadcrumbs.at(-1) ? leafLabel : item.label,
    ...(item.href ? { href: item.href === collection?.to ? collection.href : item.href } : {}),
  }));

  return dedupeBreadcrumbItems(items);
}

function dedupeBreadcrumbItems(items: readonly BreadcrumbItem[]): readonly BreadcrumbItem[] {
  // History joints and menu grouping may repeat a label and destination at
  // adjacent levels (Integrations / Integrations / Integrations). Keep the
  // deepest owner without collapsing equally named, distinct destinations.
  return items.filter((item, index) => {
    const next = items[index + 1];
    return item.label !== next?.label || item.href !== next.href;
  });
}
