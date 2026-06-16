import * as React from "react";
import { Link } from "@tanstack/react-router";

import { Glyph } from "../chrome/Glyph";
import { EmptyState } from "../fragments/EmptyState";
import { useBaseT, type BaseMessageVars } from "../i18n";
import { cn } from "../lib/cn";
import { buttonVariants } from "../ui/button";
import { ScrollArea } from "../ui/scroll-area";
import { Tabs } from "../ui/tabs";
import {
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
        "relative flex h-full min-h-0 flex-col overflow-hidden border-l border-border-subtle bg-sheet",
        className,
      )}
      style={{ width }}
    >
      <ChatterResizeHandle
        width={width}
        setWidth={setWidth}
        label={t("chatter.resize")}
      />
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

/**
 * The chatter's left-edge resize grip. The chatter lives in the shell grid's
 * `auto` column, sized by the aside's `width`, so resizing is just driving that
 * width: a pointer drag (the aside is anchored right, so dragging left widens it)
 * or arrow keys (Shift for a coarser step). The context clamps to the min/max.
 */
function ChatterResizeHandle({
  width,
  setWidth,
  label,
}: {
  width: number;
  setWidth: (width: number) => void;
  label: string;
}): React.ReactElement {
  // Read the live width through a ref so the drag/keys always start from the
  // current size without rebuilding the handlers on every resize tick.
  const widthRef = React.useRef(width);
  widthRef.current = width;

  const handlePointerDown = React.useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      if (event.button !== 0) return; // primary button only
      event.preventDefault();
      const startX = event.clientX;
      const startWidth = widthRef.current;
      // Anchored from the drag origin (not the moving handle), so the grip tracks
      // the cursor exactly even as the aside's left edge shifts under it.
      const onMove = (move: PointerEvent): void =>
        setWidth(startWidth + (startX - move.clientX));
      const onUp = (): void => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        document.body.style.userSelect = "";
        document.body.style.cursor = "";
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
      document.body.style.userSelect = "none";
      document.body.style.cursor = "col-resize";
    },
    [setWidth],
  );

  const handleKeyDown = React.useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>): void => {
      const step = event.shiftKey ? 64 : 16;
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        setWidth(widthRef.current + step);
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        setWidth(widthRef.current - step);
      }
    },
    [setWidth],
  );

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuenow={width}
      aria-valuemin={CHATTER_MIN_WIDTH}
      aria-valuemax={CHATTER_MAX_WIDTH}
      tabIndex={0}
      onPointerDown={handlePointerDown}
      onKeyDown={handleKeyDown}
      style={{ touchAction: "none" }}
      className="absolute inset-y-0 left-0 z-10 w-2 cursor-col-resize bg-transparent outline-none transition-colors hover:bg-brand focus-visible:bg-brand"
    />
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
