import * as React from "react";

import { AppRail } from "../chrome/AppRail";
import { Breadcrumb, BreadcrumbLabelProvider } from "../chrome/Breadcrumb";
import { ConsoleSubNav, useConsoleSubNav } from "../chrome/ConsoleSubNav";
import { DrawerRail } from "../chrome/DrawerRail";
import { TopBar, type TopBarProps } from "../chrome/TopBar";
import { Chatter } from "../communication/Chatter";
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
  topMenu?: TopBarProps["topMenu"];
  showChatter?: boolean;
  className?: string;
}

export function ConsoleLayout({
  children,
  topMenu,
  showChatter = true,
  className,
}: ConsoleLayoutProps): React.ReactElement {
  const [controlHost, setControlHost] =
    React.useState<HTMLDivElement | null>(null);
  const [statusHost, setStatusHost] =
    React.useState<HTMLDivElement | null>(null);
  const [primaryController, setPrimaryController] =
    React.useState<CollapsiblePane | null>(null);
  // Apps that opt into the sidebar (`sidebar: true` on their root menu) render
  // their sections in a left settings-style sub-nav *in addition to* the top bar.
  // It now rides the Workbench primary pane (collapsible + resizable), so the
  // grid stays a fixed rail + content frame whether or not the sub-nav shows.
  const { show: showSubNav } = useConsoleSubNav();

  return (
    <ChatterProvider>
      <PrimaryPaneProvider>
        <DrawerProvider>
          <ControlBandProvider host={controlHost}>
            <StatuslineProvider host={statusHost}>
              <BreadcrumbLabelProvider>
                <div
                  className={cn(
                    "console-grid h-screen w-screen bg-canvas text-fg",
                    className,
                  )}
                >
                  <AppRail className="area-rail" />
                  <TopBar
                    className="area-topbar"
                    topMenu={topMenu}
                    // The toggle shows whenever the primary pane has content —
                    // a page-published explorer *or* the settings sub-nav — which
                    // is exactly when the Workbench registers its controller.
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
                  <Breadcrumb className="area-crumbs" />
                  <div ref={setControlHost} className="area-control" />
                  <ConsoleWorkbench
                    showSubNav={showSubNav}
                    showChatter={showChatter}
                    onPrimaryController={setPrimaryController}
                  >
                    {children}
                  </ConsoleWorkbench>
                  {/* Optional statusline; the row collapses while this host is empty. */}
                  <div ref={setStatusHost} className="area-status" />
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
 * through — the settings sub-nav as the (collapsible) primary pane, the page as
 * the content, and the Chatter as the (collapsible) secondary pane. Lives inside
 * `ChatterProvider` so it can register the secondary pane's collapse controller
 * with the chatter bridge, letting the chrome `TopBar` toggle drive it (and stay
 * in sync with drag-to-collapse). The primary pane's controller is surfaced up to
 * `ConsoleLayout` so the TopBar's left-panel toggle drives it too.
 *
 * The primary pane is whatever a page publishes through `usePrimaryPane` (an
 * explorer/navigator tree); when no page publishes one, sidebar apps still get
 * their settings sub-nav. A page-published explorer wins over the sub-nav.
 */
function ConsoleWorkbench({
  showSubNav,
  showChatter,
  onPrimaryController,
  children,
}: {
  showSubNav: boolean;
  showChatter: boolean;
  onPrimaryController: (controller: CollapsiblePane | null) => void;
  children: React.ReactNode;
}): React.ReactElement {
  const { registerSecondaryController } = useChatter();
  const { node: publishedPrimary } = usePrimaryPaneContent();
  const primary =
    publishedPrimary ?? (showSubNav ? <ConsoleSubNav /> : undefined);
  return (
    <Workbench
      className="area-content"
      autoSave="console.workbench"
      primary={primary}
      secondary={showChatter ? <Chatter /> : undefined}
      onPrimaryController={onPrimaryController}
      onSecondaryController={registerSecondaryController}
    >
      {/* The content `main` landmark owns the scroll boundary; a full-height page
          (e.g. a nested Workbench) fills it without scrolling. */}
      <main className="h-full min-h-0 overflow-auto">{children}</main>
    </Workbench>
  );
}
