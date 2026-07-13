import * as React from "react";
import {
  Link,
  useMatches,
  useRouterState,
  type AnyRouteMatch,
} from "@tanstack/react-router";

import { Glyph } from "../chrome/Glyph";
import { EmptyState } from "../fragments/EmptyState";
import { useUiT, type UiMessageVars } from "../i18n";
import { cn } from "../lib/cn";
import {
  useAppRuntime,
  type ChatterContribution,
  type ChatterRoute,
  type ChatterView,
  type ChatterViewContext,
} from "../runtime";
import { buttonVariants } from "../ui/button";
import { ScrollArea } from "../ui/scroll-area";
import { Tabs } from "../ui/tabs";
import { useChatter, type ChatterTab } from "./chatter-context";

export interface ChatterProps {
  tabs?: readonly ChatterTab[];
  children?: React.ReactNode;
  composer?: React.ReactNode;
  className?: string;
}

export function Chatter({
  tabs,
  children,
  composer,
  className,
}: ChatterProps): React.ReactElement | null {
  const t = useUiT();
  const { activeTab, content, setActiveTab } = useChatter();
  const runtime = useAppRuntime();
  // Tabs merge by id (last wins): the default agent/comments/activity tabs are the
  // base, runtime chatter contributions render for the active view on top, and a
  // page's published tabs (explicit prop or context) win last. A same-id tab
  // replaces its predecessor in place; a new id appends. So a page contributing a
  // `details`/`backlinks` tab keeps the defaults it does not override.
  const viewContext = useActiveChatterView(runtime.chatterRoutes ?? []);
  const contributedTabs = React.useMemo(
    () => tabsFromContributions(runtime.chatter ?? [], viewContext),
    [runtime.chatter, viewContext],
  );
  const resolvedTabs = mergeChatterTabs(
    defaultTabs(children, t),
    contributedTabs,
    tabs ?? content?.tabs ?? [],
  );
  const resolvedComposer = composer ?? content?.composer;
  const active = resolvedTabs.some((tab) => tab.id === activeTab)
    ? activeTab
    : resolvedTabs[0]?.id;

  // Collapse is owned by the enclosing SplitPane (it collapses the pane to zero
  // width); Chatter only bails when it has no tab to show.
  if (!active) return null;

  return (
    <aside
      aria-label={t("chatter.label")}
      className={cn(
        // A pane filler — the SplitPane supplies width, separator, border, and
        // background; Chatter just lays its tabs + composer out to fill it.
        "flex h-full min-h-0 w-full flex-col overflow-hidden",
        className,
      )}
    >
      <Tabs
        value={active}
        onValueChange={(value) => setActiveTab(value)}
        variant="card"
        className="flex min-h-0 flex-1 flex-col"
      >
        <Tabs.List className="shrink-0 min-w-0 overflow-x-auto px-2 pt-2">
          {resolvedTabs.map((tab) => (
            <Tabs.Tab
              key={tab.id}
              value={tab.id}
              icon={tab.icon ? <Glyph name={tab.icon} /> : undefined}
              className="h-8 px-2 text-13 font-medium"
            >
              {tab.label}
              {typeof tab.count === "number" ? (
                <Tabs.Count>{tab.count}</Tabs.Count>
              ) : null}
            </Tabs.Tab>
          ))}
        </Tabs.List>
        {resolvedTabs.map((tab) => (
          <Tabs.Panel key={tab.id} value={tab.id} className="min-h-0 flex-1">
            <ScrollArea
              className="h-full"
              viewportClassName={cn("p-4", tab.panelClassName)}
            >
              {tab.children}
            </ScrollArea>
          </Tabs.Panel>
        ))}
      </Tabs>
      {resolvedComposer ? (
        <div className="shrink-0 border-t border-border-subtle p-3">
          {resolvedComposer}
        </div>
      ) : null}
    </aside>
  );
}

function useActiveChatterView(
  routes: readonly ChatterRoute[],
): ChatterViewContext {
  const match = useMatches({ select: leafMatch });
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  });
  return React.useMemo(() => {
    const route = routes.find((candidate) => candidate.path === match.fullPath);
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
  }, [match.fullPath, match.params, pathname, routes]);
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
  contributions: readonly ChatterContribution[],
  context: ChatterViewContext,
): readonly ChatterTab[] {
  return contributions.flatMap((contribution) => {
    if (!contribution.render) return [];
    // A render that returns null declares itself not applicable to the active
    // view (e.g. a party-only tab on a non-party record): the tab is dropped,
    // not shown empty.
    const children = contribution.render(context);
    if (children == null || children === false) return [];
    return [{
      id: contribution.id,
      label: contribution.label ?? contribution.id,
      ...(contribution.icon ? { icon: contribution.icon } : {}),
      ...(typeof contribution.count === "number" ? { count: contribution.count } : {}),
      ...(contribution.panelClassName ? { panelClassName: contribution.panelClassName } : {}),
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

function defaultTabs(
  children: React.ReactNode,
  t: (key: string, vars?: UiMessageVars) => string,
): readonly ChatterTab[] {
  return [
    {
      id: "agents",
      label: "Agents",
      icon: "agent",
      children: children ?? (
        <EmptyState
          icon="agent"
          title={t("chatter.noAgent")}
          description={t("chatter.agentHint")}
          actions={
            <Link
              className={buttonVariants({ variant: "primary", size: "sm" })}
              to="/agents"
            >
              {t("chatter.agentAction")}
            </Link>
          }
          className="min-h-48 p-4"
        />
      ),
    },
    {
      id: "comments",
      label: t("chatter.tabComments"),
      icon: "comments",
      children: (
        <EmptyState
          icon="comments"
          title={t("chatter.noComments")}
          description={t("chatter.commentsHint")}
          className="min-h-48 p-4"
        />
      ),
    },
    {
      id: "activity",
      label: t("chatter.tabActivity"),
      icon: "activity",
      children: (
        <EmptyState
          icon="activity"
          title={t("chatter.noActivity")}
          description={t("chatter.activityHint")}
          className="min-h-48 p-4"
        />
      ),
    },
  ];
}
