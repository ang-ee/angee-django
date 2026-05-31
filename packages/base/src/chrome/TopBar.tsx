import type { ReactElement, ReactNode } from "react";

import { cn } from "../lib/cn";
import { Glyph } from "./Glyph";
import { GlobalSearch } from "./GlobalSearch";
import { Systray } from "./Systray";
import { TopMenu, type TopMenuProps } from "./TopMenu";
import { UserMenu } from "./UserMenu";

export interface TopBarProps {
  title?: ReactNode;
  icon?: string;
  topMenu?: TopMenuProps["tabs"];
  className?: string;
  children?: ReactNode;
}

export function TopBar({
  title = "Console",
  icon = "layout-dashboard",
  topMenu,
  className,
  children,
}: TopBarProps): ReactElement {
  return (
    <header
      aria-label="Workspace top bar"
      className={cn(
        "area-topbar z-topbar flex h-topbar-h min-w-0 items-center gap-3 border-b border-border-on-rail bg-rail px-3 text-on-rail",
        className,
      )}
    >
      <div className="flex min-w-0 items-center gap-2">
        <Glyph name={icon} className="text-on-rail-mut" />
        <h1 className="truncate text-13 font-semibold text-on-rail-hi">
          {title}
        </h1>
      </div>
      <TopMenu tabs={topMenu} className="hidden md:flex" />
      <div className="min-w-2 flex-1" />
      {children}
      <GlobalSearch />
      <Systray />
      <UserMenu />
    </header>
  );
}
