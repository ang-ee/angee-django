import * as React from "react";
import { Link } from "@tanstack/react-router";

import { Glyph } from "../chrome/Glyph";
import { EmptyState } from "../fragments/EmptyState";
import { useBaseT, type BaseMessageVars } from "../i18n";
import { cn } from "../lib/cn";
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
  const t = useBaseT();
  const { activeTab, content, setActiveTab } = useChatter();
  const resolvedTabs = tabs ?? content?.tabs ?? defaultTabs(children, t);
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

function defaultTabs(
  children: React.ReactNode,
  t: (key: string, vars?: BaseMessageVars) => string,
): readonly ChatterTab[] {
  return [
    {
      id: "angee",
      label: "Angee",
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
