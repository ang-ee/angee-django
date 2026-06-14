import type { ReactElement, ReactNode } from "react";

import { useBaseT } from "../i18n";
import { cn } from "../lib/cn";
import { useChatter } from "../communication/chatter-context";
import { Button } from "../ui/button";
import { Tooltip } from "../ui/tooltip";
import { CommandPalette } from "./CommandPalette";
import { Glyph } from "./Glyph";
import { Systray } from "./Systray";
import { TopMenu, type TopMenuProps } from "./TopMenu";
import { UserMenu } from "./UserMenu";

export interface TopBarProps {
  /** Optional leading brand/lockup. Omit inside ConsoleShell — the rail's
   * app-switcher already carries the brand mark, so the top bar starts with
   * the menu (matching the console shell). */
  brand?: ReactNode;
  hideSearch?: boolean;
  hideSystray?: boolean;
  menuItems?: TopMenuProps["items"];
  onHelp?: () => void;
  onNotifications?: () => void;
  searchPlaceholder?: string;
  showChatterToggle?: boolean;
  showUserMenu?: boolean;
  topMenu?: TopMenuProps["tabs"];
  trailing?: ReactNode;
  className?: string;
  children?: ReactNode;
}

export function TopBar({
  brand,
  hideSearch = false,
  hideSystray = false,
  menuItems,
  onHelp,
  onNotifications,
  searchPlaceholder,
  showChatterToggle = false,
  showUserMenu = false,
  topMenu,
  trailing,
  className,
  children,
}: TopBarProps): ReactElement {
  const t = useBaseT();
  return (
    <header
      aria-label={t("chrome.topBar")}
      className={cn(
        "area-topbar z-topbar flex h-topbar-h min-w-0 items-center gap-3 border-b border-border-on-rail bg-rail px-3 pl-4 text-on-rail",
        className,
      )}
    >
      {brand}
      <TopMenu
        tabs={topMenu}
        items={menuItems}
        className="ml-1 hidden md:flex"
      />
      <div className="min-w-2 flex-1" />
      {children}
      {hideSearch ? null : (
        <CommandPalette triggerPlaceholder={searchPlaceholder} />
      )}
      {hideSystray ? null : (
        <Systray onHelp={onHelp} onNotifications={onNotifications} />
      )}
      {showUserMenu ? (
        <UserMenu
          className="size-icon-btn-md rounded-md border-0"
          side="bottom"
          align="end"
          sideOffset={6}
        />
      ) : null}
      {trailing}
      {showChatterToggle ? <ChatterToggleButton /> : null}
    </header>
  );
}

function ChatterToggleButton(): ReactElement {
  const t = useBaseT();
  const { collapsed, toggleCollapsed } = useChatter();
  const open = !collapsed;
  const label = open ? t("chrome.collapseChatter") : t("chrome.openChatter");
  return (
    <Tooltip label={label}>
      <Button
        type="button"
        variant="icon"
        size="iconSm"
        active={open}
        aria-label={label}
        aria-pressed={open}
        onClick={toggleCollapsed}
        className="text-on-rail-mut hover:bg-rail-hi hover:text-on-rail-hi"
      >
        <Glyph name="panel-right" />
      </Button>
    </Tooltip>
  );
}
