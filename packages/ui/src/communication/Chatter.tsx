import * as React from "react";
import {
  useMatches,
  useRouterState,
  type AnyRouteMatch,
} from "@tanstack/react-router";

import { Glyph } from "../chrome/Glyph";
import { EmptyState } from "../fragments/EmptyState";
import { useUiT } from "../i18n";
import { PaneToolbarHost } from "../layouts/PaneToolbar";
import { RELATED_TAB_ID } from "./chatter-containers";
import { RelatedCountProbe, useRelatedGroups } from "./RelatedPane";
import { ChatterCountProbe, useChatterCounts } from "./count-probe";
import { cn } from "../lib/cn";
import {
  useAppRuntime,
  useActiveRoute,
  useContainer,
  type ChatterSelection,
  type ChatterTabContent,
  type ChatterView,
  type ChatterViewContext,
  type ComposedContainerChild,
  modelChain,
} from "../runtime";
import {
  CHATTER_FOCUS_SEARCH_KEY,
  CHATTER_FROM_SEARCH_KEY,
  CHATTER_TAB_SEARCH_KEY,
  CHATTER_TARGET_SEARCH_KEY,
  REFINE_CREATE_ID,
} from "../runtime/route-href";
import { ScrollArea } from "../ui/scroll-area";
import { Tabs } from "../ui/tabs";
import { useChatter, type ChatterTab } from "./chatter-context";

export interface ChatterProps {
  tabs?: readonly ChatterTab[];
  className?: string;
}

/**
 * The context aside: what is around the record. Strip tabs are its persistent
 * surfaces; a drill-in (`strip: false`) shows one object in the strip's place,
 * reached from a line with a target and addressable through the URL, with the way
 * back above it. Every pane renders into its own toolbar host.
 */
export function Chatter({
  tabs,
  className,
}: ChatterProps): React.ReactElement | null {
  const t = useUiT();
  const { activeTab, setActiveTab, setCollapsed } = useChatter();
  const requested = useRouterState({
    select: (state) => {
      const search = state.location.search as Record<string, unknown>;
      const text = (key: string): string | undefined => {
        const value = search[key];
        return typeof value === "string" && value ? value : undefined;
      };
      return {
        tab: text(CHATTER_TAB_SEARCH_KEY),
        target: text(CHATTER_TARGET_SEARCH_KEY),
        focus: text(CHATTER_FOCUS_SEARCH_KEY),
        from: text(CHATTER_FROM_SEARCH_KEY),
      };
    },
    structuralSharing: true,
  });
  const requestIdentity = useRouterState({
    select: (state) => state.location.href,
  });
  const [counts, publishCount] = useChatterCounts();
  const selection = React.useMemo<ChatterSelection | undefined>(
    () => requested.target || requested.focus ? { target: requested.target, focus: requested.focus } : undefined,
    [requested.target, requested.focus],
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
    () => tabsFromContributions(activeContributions, viewContext, counts, selection),
    [activeContributions, viewContext, counts, selection],
  );
  const publishedTabs = React.useMemo(
    () => candidates.flatMap((child) => child.owner === PAGE_OWNER ? [child.content as ChatterTab] : []),
    [candidates],
  );
  const resolvedTabs = mergeChatterTabs(contributedTabs, publishedTabs);
  const stripTabs = resolvedTabs.filter((tab) => tab.strip !== false);
  const requestedId = requested.tab;
  const requestedTabAvailable = Boolean(
    requestedId && resolvedTabs.some((tab) => tab.id === requestedId),
  );
  // The URL is the navigation state: adopt its tab and selection as they stand.
  React.useEffect(() => {
    if (!requestedId) return;
    if (!requestedTabAvailable) return;
    setActiveTab(requestedId, { from: requested.from, target: requested.target, focus: requested.focus });
    setCollapsed(false);
  }, [requestIdentity, requestedId, requestedTabAvailable, requested.from, requested.target, requested.focus, setActiveTab, setCollapsed]);
  const activeId = activeTab;
  const active = resolvedTabs.some((tab) => tab.id === activeId)
    ? activeId
    : stripTabs[0]?.id;
  const activeDrillIn = resolvedTabs.find((tab) => tab.id === active && tab.strip === false);
  // The way back from a drill-in: the tab that opened it, else the first strip tab.
  const originTab = activeDrillIn
    ? resolvedTabs.find((tab) => tab.id === requested.from && tab.strip !== false) ?? stripTabs[0]
    : undefined;
  const back = React.useCallback(() => {
    if (originTab) setActiveTab(originTab.id, {});
  }, [originTab, setActiveTab]);

  // Collapse is owned by the enclosing SplitPane (it collapses the pane to zero
  // width); Chatter only bails when it has no tab to show.
  if (!active || !visible) return null;

  return (
    <aside
      aria-label={t("chatter.label")}
      className={cn(
        // A pane filler — the SplitPane supplies width, separator, border, and
        // background; Chatter just lays its tabs out to fill it.
        "chatter-pane flex h-full min-h-0 w-full min-w-0 flex-col overflow-hidden",
        className,
      )}
    >
      {activeContributions.map((contribution) =>
        contribution.content.useCount ? (
          <ChatterCountProbe
            key={`${viewContext.pathname}:${contribution.id}`}
            id={contribution.id}
            useCount={contribution.content.useCount}
            context={viewContext}
            onCount={publishCount}
          />
        ) : contribution.id === RELATED_TAB_ID ? (
          <RelatedCountProbe
            key={`${viewContext.pathname}:${contribution.id}`}
            id={contribution.id}
            context={viewContext}
            onCount={publishCount}
          />
        ) : null,
      )}
      <Tabs
        value={activeDrillIn ? (originTab?.id ?? active) : active}
        onValueChange={(value) => setActiveTab(value)}
        variant="card"
        className="flex min-h-0 flex-1 flex-col"
      >
        <Tabs.List className="flex shrink-0 min-w-0 overflow-x-auto px-2 pt-2">
          {stripTabs.map((tab) => (
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
        {activeDrillIn ? (
          <PaneToolbarHost back={originTab ? { label: originTab.label, onClick: back } : undefined} title={activeDrillIn.label}>
            {selection?.target ? (
              <ChatterPanel tab={activeDrillIn} />
            ) : (
              <EmptyState icon="activity" title={t("chatter.unavailable.title")} description={t("chatter.unavailable.description")} className="min-h-40 p-4" />
            )}
          </PaneToolbarHost>
        ) : (
          <ChatterPanels key={viewContext.pathname} tabs={stripTabs} active={active} />
        )}
      </Tabs>
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
  const context = useActiveChatterView();
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
  // The framework's Related tab shows only where an edge owner declared a group.
  const relatedGroups = useRelatedGroups(context);
  const tabs = React.useMemo(() => children.filter((child) => {
    if (child.owner === PAGE_OWNER) return true;
    const tab = child.content as ChatterTabContent;
    if (!tab.render) return false;
    if (child.address === "record#aside" && context.view.kind !== "record") return false;
    if (child.id === RELATED_TAB_ID && relatedGroups.length === 0) return false;
    return tab.when?.(context) ?? true;
  }), [children, context, relatedGroups]);
  // The aside shows when a strip tab exists; drill-ins alone never open it.
  const visible = tabs.some((child) => child.owner === PAGE_OWNER || (child.content as ChatterTabContent).strip !== false);
  return { context, visible, tabs };
}

/** Strip tabs mount on first visit and then stay mounted for that record, so drafts survive switching. */
function ChatterPanels({ tabs, active }: { tabs: readonly ChatterTab[]; active: string }): React.ReactElement {
  const [visited, setVisited] = React.useState<readonly string[]>([active]);
  React.useEffect(() => {
    setVisited((current) => current.includes(active) ? current : [...current, active]);
  }, [active]);
  return <>{tabs.map((tab) => (
    <Tabs.Panel key={tab.id} value={tab.id} keepMounted={visited.includes(tab.id)} className="flex min-h-0 flex-1 flex-col">
      <ChatterPanel tab={tab} />
    </Tabs.Panel>
  ))}</>;
}

/** One pane: its own toolbar host above its scroll area. */
function ChatterPanel({ tab }: { tab: ChatterTab }): React.ReactElement {
  return (
    <PaneToolbarHost>
      {/* base-ui's ScrollArea.Content sets inline `min-width: fit-content`; only an
          inline `contentStyle` override lets long content shrink and wrap instead of
          overflowing horizontally, since a `min-w-0` class can never beat that inline
          style. */}
      <ScrollArea
        className="min-h-0 flex-1"
        viewportClassName={cn("overflow-x-hidden p-4", tab.panelClassName)}
        contentClassName="w-full max-w-full"
        contentStyle={{ minWidth: 0 }}
      >
        {tab.children}
      </ScrollArea>
    </PaneToolbarHost>
  );
}

/** The current route and record context, shared by the aside and shell drawers. */
export function useActiveChatterView(): ChatterViewContext {
  const routes = useAppRuntime().chatterRoutes ?? [];
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
    // A record view names a saved model row. The create sentinel, a dashboard
    // key, a board or a merge pair has no record capabilities.
    const record = selectedId !== undefined && selectedId !== REFINE_CREATE_ID && Boolean(route?.modelLabel);
    const view: ChatterView = {
      kind: record ? "record" : "dashboard",
      type: route?.viewType ?? viewTypeFromPath(pathname),
      ...(record ? { sqid: selectedId } : {}),
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
  selection: ChatterSelection | undefined,
): readonly ChatterTab[] {
  return contributions.flatMap(({ id, content: tab }) => {
    if (!tab.render) return [];
    const children = tab.render(context, selection);
    if (children == null || children === false) return [];
    const count = counts[id] ?? tab.count;
    return [{
      id,
      label: tab.label ?? id,
      ...(tab.icon ? { icon: tab.icon } : {}),
      ...(typeof count === "number" ? { count } : {}),
      ...(tab.panelClassName ? { panelClassName: tab.panelClassName } : {}),
      ...(tab.strip === false ? { strip: false } : {}),
      children,
    }];
  });
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
