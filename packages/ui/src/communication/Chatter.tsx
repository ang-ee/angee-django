import * as React from "react";
import {
  useMatches,
  useRouterState,
  type AnyRouteMatch,
} from "@tanstack/react-router";

import { Glyph } from "../chrome/Glyph";
import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import {
  useAppRuntime,
  useActiveRoute,
  useContainer,
  type ChatterRoute,
  type ChatterTabContent,
  type ChatterView,
  type ChatterViewContext,
  type ComposedContainerChild,
  modelChain,
} from "../runtime";
import { ScrollArea } from "../ui/scroll-area";
import { Tabs } from "../ui/tabs";
import { CHATTER_TAB_SEARCH_KEY, useChatter, type ChatterTab } from "./chatter-context";

export interface ChatterProps {
  tabs?: readonly ChatterTab[];
  composer?: React.ReactNode;
  className?: string;
}

export function Chatter({
  tabs,
  composer,
  className,
}: ChatterProps): React.ReactElement | null {
  const t = useUiT();
  const { activeTab, content, setActiveTab, setCollapsed } = useChatter();
  const requestedTab = useRouterState({
    select: (state) => {
      const value = (state.location.search as Record<string, unknown>)[CHATTER_TAB_SEARCH_KEY];
      return typeof value === "string" && value ? value : null;
    },
  });
  const requestIdentity = useRouterState({
    select: (state) => state.location.href,
  });
  const [counts, setCounts] = React.useState<Record<string, number>>({});
  const publishCount = React.useCallback(
    (id: string, count: number | undefined) => {
      setCounts((current) => {
        const currentCount = current[id];
        if (count === undefined) {
          if (currentCount === undefined) return current;
          const next = { ...current };
          delete next[id];
          return next;
        }
        if (currentCount === count) return current;
        return { ...current, [id]: count };
      });
    },
    [],
  );
  // The aside's tabs are `record#aside` / `<model>#aside` children for this view,
  // narrowed by the layers, in their composed order; a page's published tabs
  // follow, a same-id tab replacing its predecessor in place.
  const { context: viewContext, visible, tabs: candidates } = useChatterPresentation(tabs);
  const activeContributions = React.useMemo(
    () => candidates.filter((child): child is ComposedContainerChild<ChatterTabContent> => child.owner !== PAGE_OWNER),
    [candidates],
  );
  const contributedTabs = React.useMemo(
    () => tabsFromContributions(activeContributions, viewContext, counts),
    [activeContributions, viewContext, counts],
  );
  const publishedTabs = React.useMemo(
    () => candidates.flatMap((child) => child.owner === PAGE_OWNER ? [child.content as ChatterTab] : []),
    [candidates],
  );
  const resolvedTabs = mergeChatterTabs(contributedTabs, publishedTabs);
  // A `?chatterTab=` link or a caller may still name a tab by the id it carried before (one release).
  const resolveTabId = (id: string): string =>
    activeContributions.find((child) => child.id === id || child.content.aliases?.includes(id))?.id ?? id;
  const requestedId = requestedTab ? resolveTabId(requestedTab) : null;
  const resolvedComposer = composer ?? content?.composer;
  const requestedTabAvailable = Boolean(
    requestedId && resolvedTabs.some((tab) => tab.id === requestedId),
  );
  React.useEffect(() => {
    if (!requestedId) return;
    if (!requestedTabAvailable) return;
    setActiveTab(requestedId);
    setCollapsed(false);
  }, [requestIdentity, requestedId, requestedTabAvailable, setActiveTab, setCollapsed]);
  const activeId = activeTab ? resolveTabId(activeTab) : activeTab;
  const active = resolvedTabs.some((tab) => tab.id === activeId)
    ? activeId
    : resolvedTabs[0]?.id;

  // Collapse is owned by the enclosing SplitPane (it collapses the pane to zero
  // width); Chatter only bails when it has no tab to show.
  if (!active || !visible) return null;

  return (
    <aside
      aria-label={t("chatter.label")}
      className={cn(
        // A pane filler — the SplitPane supplies width, separator, border, and
        // background; Chatter just lays its tabs + composer out to fill it.
        "chatter-pane flex h-full min-h-0 w-full min-w-0 flex-col overflow-hidden",
        className,
      )}
    >
      {activeContributions.map((contribution) =>
        contribution.content.useCount ? (
          <ChatterCountProbe
            key={contribution.id}
            id={contribution.id}
            useCount={contribution.content.useCount}
            context={viewContext}
            onCount={publishCount}
          />
        ) : null,
      )}
      <Tabs
        value={active}
        onValueChange={(value) => setActiveTab(value)}
        variant="card"
        className="flex min-h-0 flex-1 flex-col"
      >
        <Tabs.List className="flex shrink-0 min-w-0 overflow-x-auto px-2 pt-2">
          {resolvedTabs.map((tab) => (
            <Tabs.Tab
              key={tab.id}
              value={tab.id}
              onClick={() => setActiveTab(tab.id)}
              icon={tab.icon ? <Glyph name={tab.icon} /> : undefined}
              className="h-8 min-w-fit flex-1 px-2 text-13 font-medium"
            >
              <span
                className={cn(tab.icon && "chatter-tab-label")}
                aria-hidden={tab.icon ? true : undefined}
              >
                {tab.label}
              </span>
              {tab.icon ? <span className="sr-only">{tab.label}</span> : null}
              {typeof tab.count === "number" ? (
                <Tabs.Count className="shrink-0">{tab.count}</Tabs.Count>
              ) : null}
            </Tabs.Tab>
          ))}
        </Tabs.List>
        <ChatterPanels key={viewContext.pathname} tabs={resolvedTabs} active={active} />
      </Tabs>
      {resolvedComposer ? (
        <div className="min-w-0 shrink-0 overflow-hidden border-t border-border-subtle p-3">
          {resolvedComposer}
        </div>
      ) : null}
    </aside>
  );
}

const PAGE_OWNER = "page";

/**
 * The aside's candidate tabs for the active view, which decide whether it shows:
 * kind-level children on record views, a model's own children on its pages, and
 * the page's published tabs, each narrowed by the layers and its `when`.
 */
export function useChatterPresentation(explicitTabs?: readonly ChatterTab[]): {
  context: ChatterViewContext;
  visible: boolean;
  tabs: readonly ComposedContainerChild<ChatterTabContent | ChatterTab>[];
} {
  const runtime = useAppRuntime();
  const context = useActiveChatterView(runtime.chatterRoutes ?? []);
  const { content } = useChatter();
  const published = explicitTabs ?? content?.tabs;
  const models = React.useMemo(
    () => modelChain(context.route?.canonicalLabel, context.route?.modelLabel),
    [context.route?.canonicalLabel, context.route?.modelLabel],
  );
  const extra = React.useMemo(
    () => (published ?? []).map((tab) => ({ id: tab.id, owner: PAGE_OWNER, address: "record#aside", content: tab })),
    [published],
  );
  const children = useContainer<ChatterTabContent | ChatterTab>("record#aside", { models, extra });
  const tabs = React.useMemo(() => children.filter((child) => {
    if (child.owner === PAGE_OWNER) return true;
    const tab = child.content as ChatterTabContent;
    if (!tab.render) return false;
    if (child.address === "record#aside" && context.view.kind !== "record") return false;
    return tab.when?.(context) ?? true;
  }), [children, context]);
  return { context, visible: tabs.length > 0, tabs };
}

/** Visit lazily, then retain this record's draft input while peeking at sources. */
function ChatterPanels({ tabs, active }: { tabs: readonly ChatterTab[]; active: string }): React.ReactElement {
  const [visited, setVisited] = React.useState<readonly string[]>([active]);
  React.useEffect(() => {
    setVisited((current) => current.includes(active) ? current : [...current, active]);
  }, [active]);
  return <>{tabs.map((tab) => (
    <Tabs.Panel key={tab.id} value={tab.id} keepMounted={visited.includes(tab.id)} className="min-h-0 flex-1">
      {/* base-ui's ScrollArea.Content sets inline `min-width: fit-content`; only an
          inline `contentStyle` override lets long content shrink and wrap instead of
          overflowing horizontally, since a `min-w-0` class can never beat that inline
          style. */}
      <ScrollArea
        className="h-full"
        viewportClassName={cn("overflow-x-hidden p-4", tab.panelClassName)}
        contentClassName="w-full max-w-full"
        contentStyle={{ minWidth: 0 }}
      >
        {tab.children}
      </ScrollArea>
    </Tabs.Panel>
  ))}</>;
}

function useActiveChatterView(
  routes: readonly ChatterRoute[],
): ChatterViewContext {
  const match = useMatches({ select: leafMatch });
  const route = useActiveRoute(routes);
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  });
  return React.useMemo(() => {
    const params = normalizeRouteParams(match.params);
    const selectedId =
      route?.recordParam && params[route.recordParam]
        ? params[route.recordParam]
        : undefined;
    const view: ChatterView = {
      kind: selectedId ? "record" : "dashboard",
      type: route?.viewType ?? viewTypeFromPath(pathname),
      ...(selectedId ? { sqid: selectedId } : {}),
      ...(Object.keys(params).length > 0 ? { params } : {}),
    };
    return {
      pathname,
      params,
      ...(route ? { route } : {}),
      view,
    };
  }, [match.params, pathname, route]);
}

function leafMatch(matches: readonly AnyRouteMatch[]): {
  fullPath: string;
  params: Record<string, unknown>;
} {
  const match = matches.at(-1);
  return {
    fullPath: match?.fullPath ?? "/",
    params: match?.params ?? {},
  };
}

function normalizeRouteParams(
  params: Readonly<Record<string, unknown>>,
): Record<string, string> {
  const normalized: Record<string, string> = {};
  for (const [key, value] of Object.entries(params)) {
    if (value == null) continue;
    normalized[key] = String(value);
  }
  return normalized;
}

function viewTypeFromPath(pathname: string): string {
  const segments = pathname.split("/").filter(Boolean);
  return segments.length > 0 ? segments.join("/") : "home";
}

function tabsFromContributions(
  contributions: readonly ComposedContainerChild<ChatterTabContent>[],
  context: ChatterViewContext,
  counts: Readonly<Record<string, number>>,
): readonly ChatterTab[] {
  return contributions.flatMap(({ id, content: tab }) => {
    if (!tab.render) return [];
    const children = tab.render(context);
    if (children == null || children === false) return [];
    const count = counts[id] ?? tab.count;
    return [{
      id,
      label: tab.label ?? id,
      ...(tab.icon ? { icon: tab.icon } : {}),
      ...(typeof count === "number" ? { count } : {}),
      ...(tab.panelClassName ? { panelClassName: tab.panelClassName } : {}),
      children,
    }];
  });
}

interface ChatterCountProbeProps {
  id: string;
  useCount: (context: ChatterViewContext) => number | undefined;
  context: ChatterViewContext;
  onCount: (id: string, count: number | undefined) => void;
}

function ChatterCountProbe({
  id,
  useCount,
  context,
  onCount,
}: ChatterCountProbeProps): null {
  const count = useCount(context);
  React.useEffect(() => {
    onCount(id, count);
    return () => onCount(id, undefined);
  }, [id, count, onCount]);
  return null;
}

// Merge tab groups by id (last wins): a same-id tab replaces its predecessor in
// place; a new id appends. Earlier groups are the base, later groups override.
function mergeChatterTabs(
  ...groups: readonly (readonly ChatterTab[])[]
): readonly ChatterTab[] {
  const byId = new Map<string, ChatterTab>();
  for (const group of groups) {
    for (const tab of group) byId.set(tab.id, tab);
  }
  return [...byId.values()];
}
