import * as React from "react";

import { AppRail } from "../chrome/AppRail";
import { Breadcrumb, BreadcrumbLabelProvider } from "../chrome/Breadcrumb";
import { ConsoleSubNav, useConsoleSubNav } from "../chrome/ConsoleSubNav";
import { TopBar, type TopBarProps } from "../chrome/TopBar";
import { Chatter } from "../communication/Chatter";
import { ChatterProvider, useChatter } from "../communication/chatter-context";
import { cn } from "../lib/cn";
import type { CollapsiblePane } from "../page";
import { ControlBandProvider } from "./ControlBand";
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
                primaryPane={
                  showSubNav && primaryController
                    ? {
                        collapsed: primaryController.collapsed,
                        toggle: primaryController.toggle,
                      }
                    : undefined
                }
                showChatterToggle={showChatter}
                showUserMenu
              />
              <ConsoleWorkbench
                showSubNav={showSubNav}
                showChatter={showChatter}
                onPrimaryController={setPrimaryController}
                setControlHost={setControlHost}
                setStatusHost={setStatusHost}
              >
                {children}
              </ConsoleWorkbench>
            </div>
          </BreadcrumbLabelProvider>
        </StatuslineProvider>
      </ControlBandProvider>
    </ChatterProvider>
  );
}

/**
 * The console region under the top bar: the single `Workbench` every console
 * page flows through. Its primary/secondary panes span from the top bar to the
 * bottom edge; the content pane owns the vertical chrome stack (breadcrumbs,
 * control band, page body, optional statusline).
 */
function ConsoleWorkbench({
  showSubNav,
  showChatter,
  onPrimaryController,
  setControlHost,
  setStatusHost,
  children,
}: {
  showSubNav: boolean;
  showChatter: boolean;
  onPrimaryController: (controller: CollapsiblePane | null) => void;
  setControlHost: (node: HTMLDivElement | null) => void;
  setStatusHost: (node: HTMLDivElement | null) => void;
  children: React.ReactNode;
}): React.ReactElement {
  const { registerSecondaryController } = useChatter();
  return (
    <Workbench
      className="area-content"
      autoSave="console.workbench"
      primary={showSubNav ? <ConsoleSubNav /> : undefined}
      secondary={showChatter ? <Chatter /> : undefined}
      onPrimaryController={onPrimaryController}
      onSecondaryController={registerSecondaryController}
    >
      <div className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden">
        <Breadcrumb />
        <div ref={setControlHost} className="area-control min-w-0 shrink-0" />
        {/* The shell owns the content scroll boundary, as the old `main` did; a
            full-height page (e.g. a nested Workbench) fills it without scrolling. */}
        <main className="min-h-0 min-w-0 flex-1 overflow-auto">{children}</main>
        {/* Optional statusline; this host collapses while empty. */}
        <div ref={setStatusHost} className="area-status min-w-0 shrink-0" />
      </div>
    </Workbench>
  );
}
