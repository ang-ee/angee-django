import * as React from "react";

import { AppRail } from "../chrome/AppRail";
import { Breadcrumb, type BreadcrumbItem } from "../chrome/Breadcrumb";
import { ConsoleSubNav, useConsoleSubNav } from "../chrome/ConsoleSubNav";
import { TopBar, type TopBarProps } from "../chrome/TopBar";
import { Chatter } from "../communication/Chatter";
import { ChatterProvider } from "../communication/chatter-context";
import { cn } from "../lib/cn";
import { ControlBandProvider } from "./ControlBand";
import { StatuslineProvider } from "./Statusline";

export interface ConsoleShellProps {
  children: React.ReactNode;
  title?: React.ReactNode;
  icon?: string;
  breadcrumbs?: readonly BreadcrumbItem[];
  topMenu?: TopBarProps["topMenu"];
  showChatter?: boolean;
  className?: string;
}

export function ConsoleShell({
  children,
  breadcrumbs,
  topMenu,
  showChatter = true,
  className,
}: ConsoleShellProps): React.ReactElement {
  const [controlHost, setControlHost] =
    React.useState<HTMLDivElement | null>(null);
  const [statusHost, setStatusHost] =
    React.useState<HTMLDivElement | null>(null);
  // Apps that opt into the sidebar (`sidebar: true` on their root menu) render
  // their sections in a left settings-style sub-nav *in addition to* the top bar.
  // The grid grows a `sidebar` column; the top bar is unchanged either way.
  const { show: showSubNav } = useConsoleSubNav();

  return (
    <ChatterProvider>
      <ControlBandProvider host={controlHost}>
        <StatuslineProvider host={statusHost}>
          <div
            className={cn(
              showSubNav ? "console-grid-sidebar" : "console-grid",
              "h-screen w-screen bg-canvas text-fg",
              className,
            )}
          >
            <AppRail className="area-rail" />
            {showSubNav ? <ConsoleSubNav /> : null}
            <TopBar
              className="area-topbar"
              topMenu={topMenu}
              showChatterToggle={showChatter}
              showUserMenu
            />
            <Breadcrumb className="area-crumbs" items={breadcrumbs} />
            <div ref={setControlHost} className="area-control" />
            <main className="area-content min-h-0 min-w-0 overflow-auto bg-canvas">
              {children}
            </main>
            {showChatter ? <Chatter className="area-chatter" /> : null}
            {/* Optional statusline; the row collapses while this host is empty. */}
            <div ref={setStatusHost} className="area-status" />
          </div>
        </StatuslineProvider>
      </ControlBandProvider>
    </ChatterProvider>
  );
}
