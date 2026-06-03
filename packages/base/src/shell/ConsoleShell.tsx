import * as React from "react";

import { AppRail } from "../chrome/AppRail";
import {
  Breadcrumb,
  BreadcrumbProvider,
  type BreadcrumbItem,
} from "../chrome/Breadcrumb";
import { TopBar, type TopBarProps } from "../chrome/TopBar";
import { Chatter } from "../communication/Chatter";
import { ChatterProvider } from "../communication/chatter-context";
import { cn } from "../lib/cn";
import { ControlBandProvider } from "./ControlBand";

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
  title = "Console",
  icon = "home",
  breadcrumbs,
  topMenu,
  showChatter = true,
  className,
}: ConsoleShellProps): React.ReactElement {
  const [controlHost, setControlHost] =
    React.useState<HTMLDivElement | null>(null);
  const trail = React.useMemo<readonly BreadcrumbItem[]>(
    () => breadcrumbs ?? [{ label: title }],
    [breadcrumbs, title],
  );

  return (
    <ChatterProvider>
      <BreadcrumbProvider initialTrail={trail}>
        <ControlBandProvider host={controlHost}>
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
              showChatterToggle={showChatter}
              showUserMenu
            />
            <Breadcrumb className="area-crumbs" />
            <div ref={setControlHost} className="area-control" />
            <main className="area-content min-h-0 min-w-0 overflow-auto bg-canvas">
              {children}
            </main>
            {showChatter ? <Chatter className="area-chatter" /> : null}
          </div>
        </ControlBandProvider>
      </BreadcrumbProvider>
    </ChatterProvider>
  );
}
