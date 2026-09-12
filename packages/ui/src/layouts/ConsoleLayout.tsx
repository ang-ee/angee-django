import * as React from "react";

import { AppRail } from "../chrome/AppRail";
import { BreadcrumbLabelProvider } from "../chrome/Breadcrumb";
import { DrawerRail } from "../chrome/DrawerRail";
import { TopBar } from "../chrome/TopBar";
import {
  Chatter,
  useChatterHasContent,
  useChatterRailStartsOpen,
} from "../communication/Chatter";
import { ChatterProvider, useChatter } from "../communication/chatter-context";
import { cn } from "../lib/cn";
import type { CollapsiblePane } from "../page";
import { ControlBandProvider } from "./ControlBand";
import { DrawerProvider } from "./drawer-context";
import { DrawerOverlay } from "./DrawerOverlay";
import { PrimaryPaneProvider, usePrimaryPaneContent } from "./primary-pane-context";
import { StatuslineProvider } from "./Statusline";
import { Workbench } from "./Workbench";

export interface ConsoleLayoutProps {
  children: React.ReactNode;
  showChatter?: boolean;
  className?: string;
}

export function ConsoleLayout({
  children,
  showChatter = true,
  className,
}: ConsoleLayoutProps): React.ReactElement {
  const [controlHost, setControlHost] =
    React.useState<HTMLDivElement | null>(null);
  const [statusHost, setStatusHost] =
    React.useState<HTMLDivElement | null>(null);
  const [primaryController, setPrimaryController] =
    React.useState<CollapsiblePane | null>(null);
  const [railWidth, setRailWidth] = React.useState<string | null>(null);
  const handlePrimaryController = React.useCallback(
    (controller: CollapsiblePane | null) => {
      setPrimaryController((current) =>
        current === controller
        || (
          current != null
          && controller != null
          && current.collapsed === controller.collapsed
          && current.toggle === controller.toggle
        )
          ? current
          : controller,
      );
    },
    [],
  );
  return (
    <ChatterProvider>
      <PrimaryPaneProvider>
        <DrawerProvider>
          <ControlBandProvider host={controlHost}>
            <StatuslineProvider host={statusHost}>
              <BreadcrumbLabelProvider>
                <div
                  // The CSS var() fallback owns the collapsed default; the
                  // style entry exists only once the rail has published.
                  style={railWidth
                    ? { "--rail-current-w": railWidth } as React.CSSProperties
                    : undefined}
                  className={cn(
                    "console-grid min-h-screen w-screen bg-canvas text-fg",
                    className,
                  )}
                >
                  <AppRail onWidthChange={setRailWidth} />
                  <TopBar
                    className="area-topbar"
                    primaryPane={
                      primaryController
                        ? {
                            collapsed: primaryController.collapsed,
                            toggle: primaryController.toggle,
                          }
                        : undefined
                    }
                    showChatterToggle={showChatter}
                    showUserMenu
                  />
                  <div ref={setControlHost} className="area-control" />
                  <ConsoleWorkbench
                    showChatter={showChatter}
                    onPrimaryController={handlePrimaryController}
                  >
                    {children}
                  </ConsoleWorkbench>
                  {/* Optional statusline; the row collapses while this host is empty. */}
                  <div
                    ref={setStatusHost}
                    className="area-status console-statusline-host"
                  />
                </div>
                {/* Drawers live at shell level (above the grid + router outlet) so
                    the open drawer's content mounts once and survives navigation.
                    Overlays render first, rails last, so a tab stays clickable to
                    toggle its drawer closed even while the panel is open. */}
                <DrawerOverlay edge="right" />
                <DrawerOverlay edge="bottom" />
                <DrawerRail edge="right" />
                <DrawerRail edge="bottom" />
              </BreadcrumbLabelProvider>
            </StatuslineProvider>
          </ControlBandProvider>
        </DrawerProvider>
      </PrimaryPaneProvider>
    </ChatterProvider>
  );
}

/**
 * The console content region: the single `Workbench` every console page flows
 * through — page-published context as the (collapsible) primary pane, the page
 * as content, and Chatter as the (collapsible) secondary pane. Lives inside
 * `ChatterProvider` so it can register the secondary pane's collapse controller
 * with the chatter bridge, letting the chrome `TopBar` toggle drive it (and stay
 * in sync with drag-to-collapse). The primary pane's controller is surfaced up to
 * `ConsoleLayout` so the TopBar's left-panel toggle drives it too.
 *
 * The primary pane exists only while a page publishes a contextual explorer.
 */
function ConsoleWorkbench({
  showChatter,
  onPrimaryController,
  children,
}: {
  showChatter: boolean;
  onPrimaryController: (controller: CollapsiblePane | null) => void;
  children: React.ReactNode;
}): React.ReactElement {
  const { registerSecondaryController } = useChatter();
  const { node: publishedPrimary } = usePrimaryPaneContent();
  // No record to discuss means no aside: an empty rail otherwise sits over the
  // page, and on a board it covers a lane whose cards then cannot be grabbed.
  const chatterHasContent = useChatterHasContent();
  // A record whose addon asked for an open rail starts with one, at widths that
  // have room for it; everything else keeps the collapsed strip.
  const chatterStartsOpen = useChatterRailStartsOpen();
  return (
    <Workbench
      className="area-content"
      // Two buckets, because the saved size is the pane's only memory and it is
      // shared across routes: with one key, opening the rail on a task record
      // would leave it open over every other page until someone closed it, and
      // the default below would never be reached again on any of them.
      autoSave={chatterStartsOpen ? "console.workbench.chatter" : "console.workbench"}
      scrollMode="browser"
      secondarySize={30}
      secondaryDefaultCollapsed={!chatterStartsOpen}
      primary={
        publishedPrimary != null ? (
          <ControlBandProvider host={undefined}>
            {publishedPrimary}
          </ControlBandProvider>
        ) : undefined
      }
      secondary={showChatter && chatterHasContent ? (
        <ControlBandProvider host={undefined}>
          <Chatter />
        </ControlBandProvider>
      ) : undefined}
      onPrimaryController={onPrimaryController}
      onSecondaryController={registerSecondaryController}
    >
      {/* The browser owns route scrolling; the statusline remains pinned chrome. */}
      <main className="console-browser-scroll-main">{children}</main>
    </Workbench>
  );
}
