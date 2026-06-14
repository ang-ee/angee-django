import * as React from "react";
import {
  Group as PanelGroup,
  Panel,
  Separator as PanelResizeHandle,
  type PanelSize,
} from "react-resizable-panels";

import { Glyph } from "../chrome/Glyph";
import { EmptyState } from "../fragments/EmptyState";
import { useBaseT, type BaseMessageVars } from "../i18n";
import { cn } from "../lib/cn";
import { ScrollArea } from "../ui/scroll-area";
import { Tabs } from "../ui/tabs";
import {
  CHATTER_DEFAULT_WIDTH,
  CHATTER_MAX_WIDTH,
  CHATTER_MIN_WIDTH,
  useChatter,
  type ChatterTab,
} from "./chatter-context";

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
  const { activeTab, collapsed, content, setActiveTab, setWidth, width } =
    useChatter();
  const resolvedTabs = tabs ?? content?.tabs ?? defaultTabs(children, t);
  const resolvedComposer = composer ?? content?.composer;
  const active = resolvedTabs.some((tab) => tab.id === activeTab)
    ? activeTab
    : resolvedTabs[0]?.id;

  if (collapsed || !active) return null;

  return (
    <aside
      aria-label={t("chatter.label")}
      className={cn(
        "h-full min-h-0 overflow-hidden border-l border-border-subtle bg-sheet",
        className,
      )}
      style={{ width }}
    >
      <PanelGroup
        id="chatter"
        orientation="horizontal"
        className="h-full min-h-0"
      >
        <PanelResizeHandle
          id="chatter-resize"
          aria-label={t("chatter.resize")}
          className="w-1.5 cursor-col-resize bg-transparent outline-none transition-colors hover:bg-brand focus-visible:bg-brand"
        />
        <Panel
          id="chatter-panel"
          defaultSize={`${width}px`}
          minSize={`${CHATTER_MIN_WIDTH}px`}
          maxSize={`${CHATTER_MAX_WIDTH}px`}
          groupResizeBehavior="preserve-pixel-size"
          onResize={(size) => updateWidth(size, setWidth)}
          className="h-full min-h-0"
        >
          <Tabs
            value={active}
            onValueChange={(value) => setActiveTab(value)}
            variant="card"
            className="flex h-full min-h-0 flex-col"
          >
            <Tabs.List className="shrink-0 min-w-0 overflow-x-auto px-2 pt-2">
              {resolvedTabs.map((tab) => (
                <Tabs.Tab
                  key={tab.id}
                  value={tab.id}
                  icon={tab.icon ? <Glyph name={tab.icon} /> : undefined}
                  className="h-8 px-2 text-13 font-medium leading-5"
                >
                  {tab.label}
                  {typeof tab.count === "number" ? (
                    <Tabs.Count>{tab.count}</Tabs.Count>
                  ) : null}
                </Tabs.Tab>
              ))}
            </Tabs.List>
            {resolvedTabs.map((tab) => (
              <Tabs.Panel
                key={tab.id}
                value={tab.id}
                className="min-h-0 flex-1"
              >
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
            <div className="border-t border-border-subtle p-3">
              {resolvedComposer}
            </div>
          ) : null}
        </Panel>
      </PanelGroup>
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

function updateWidth(
  size: PanelSize,
  setWidth: (width: number) => void,
): void {
  if (size.inPixels > 0) setWidth(size.inPixels);
}
