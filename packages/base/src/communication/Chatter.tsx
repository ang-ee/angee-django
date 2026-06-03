import * as React from "react";
import {
  Group as PanelGroup,
  Panel,
  Separator as PanelResizeHandle,
  type PanelSize,
} from "react-resizable-panels";

import { Glyph } from "../chrome/Glyph";
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
  const { activeTab, collapsed, content, setActiveTab, setWidth, width } =
    useChatter();
  const resolvedTabs = tabs ?? content?.tabs ?? defaultTabs(children);
  const resolvedComposer = composer ?? content?.composer;
  const active = resolvedTabs.some((tab) => tab.id === activeTab)
    ? activeTab
    : resolvedTabs[0]?.id;

  if (collapsed || !active) return null;

  return (
    <aside
      aria-label="Chatter"
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
          aria-label="Resize chatter"
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

function defaultTabs(children: React.ReactNode): readonly ChatterTab[] {
  return [
    {
      id: "angee",
      label: "Angee",
      icon: "agent",
      children: children ?? <AngeeEmptyState />,
    },
    {
      id: "comments",
      label: "Comments",
      icon: "comments",
      children: <EmptyState title="No comments yet" body="Comments will appear here." />,
    },
    {
      id: "activity",
      label: "Activity",
      icon: "activity",
      children: <EmptyState title="No activity yet" body="Record activity will appear here." />,
    },
  ];
}

function AngeeEmptyState(): React.ReactElement {
  return <EmptyState title="No agent yet" body="Set up your assistant" />;
}

function EmptyState({
  title,
  body,
}: {
  title: React.ReactNode;
  body: React.ReactNode;
}): React.ReactElement {
  return (
    <div className="grid min-h-48 place-content-center gap-2 text-center">
      <div className="mx-auto grid size-10 place-content-center rounded-md bg-accent-soft text-accent-soft-text [&_.glyph]:size-5">
        <Glyph name="agent" />
      </div>
      <p className="text-sm font-semibold text-fg">{title}</p>
      <p className="text-13 text-fg-muted">{body}</p>
    </div>
  );
}

function updateWidth(
  size: PanelSize,
  setWidth: (width: number) => void,
): void {
  if (size.inPixels > 0) setWidth(size.inPixels);
}
