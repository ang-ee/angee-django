import type { ReactElement, ReactNode } from "react";

import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { useThemePreference, type ThemePreference } from "../lib/theme";
import { useChatter } from "../communication/chatter-context";
import { barVariants } from "../layouts/bar";
import { Button } from "../ui/button";
import { Tooltip } from "../ui/tooltip";
import { CommandPalette } from "./CommandPalette";
import { Glyph } from "./Glyph";
import { Systray } from "./Systray";
import { TopMenu, type TopMenuProps } from "./TopMenu";
import { UserMenu } from "./UserMenu";

export interface TopBarProps {
  /** Optional leading brand/lockup. Omit inside ConsoleLayout — the rail's
   * app-switcher already carries the brand mark, so the top bar starts with
   * the menu (matching the console layout). */
  brand?: ReactNode;
  hideSearch?: boolean;
  hideSystray?: boolean;
  hideThemeToggle?: boolean;
  menuItems?: TopMenuProps["items"];
  onHelp?: () => void;
  onNotifications?: () => void;
  primaryPane?: {
    collapsed: boolean;
    toggle: () => void;
  };
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
  hideThemeToggle = false,
  menuItems,
  onHelp,
  onNotifications,
  primaryPane,
  searchPlaceholder,
  showChatterToggle = false,
  showUserMenu = false,
  topMenu,
  trailing,
  className,
  children,
}: TopBarProps): ReactElement {
  const t = useUiT();
  return (
    <header
      aria-label={t("chrome.topBar")}
      className={cn(
        barVariants({ height: "topbar", edge: "bottom", tone: "rail", gap: 3 }),
        // Grid placement + stacking, plus TopBar's asymmetric leading pad.
        "area-topbar z-topbar px-3 pl-4",
        className,
      )}
    >
      {brand}
      {primaryPane ? <PrimaryPaneToggleButton pane={primaryPane} /> : null}
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
      {hideThemeToggle ? null : <ThemeToggleButton />}
      {showUserMenu ? (
        <UserMenu
          className="size-icon-btn-md rounded-6 border-0"
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

function PrimaryPaneToggleButton({
  pane,
}: {
  pane: NonNullable<TopBarProps["primaryPane"]>;
}): ReactElement {
  const t = useUiT();
  const open = !pane.collapsed;
  const label = open
    ? t("chrome.collapsePrimaryPane")
    : t("chrome.openPrimaryPane");
  return (
    <Tooltip label={label}>
      <Button
        type="button"
        variant="icon"
        size="iconSm"
        active={open}
        aria-label={label}
        aria-pressed={open}
        onClick={pane.toggle}
        className="text-on-rail-mut hover:bg-rail-hi hover:text-on-rail-hi"
      >
        <Glyph name="panel-left" />
      </Button>
    </Tooltip>
  );
}

function ThemeToggleButton(): ReactElement {
  const t = useUiT();
  const { resolved, setPreference } = useThemePreference();
  const next: ThemePreference = resolved === "dark" ? "light" : "dark";
  const label =
    next === "dark"
      ? t("chrome.switchToDarkTheme")
      : t("chrome.switchToLightTheme");

  return (
    <Tooltip label={label}>
      <Button
        type="button"
        variant="icon"
        size="iconSm"
        aria-label={label}
        aria-pressed={resolved === "dark"}
        onClick={() => setPreference(next)}
        className="text-on-rail-mut hover:bg-rail-hi hover:text-on-rail-hi"
      >
        <Glyph name={next === "dark" ? "moon" : "sun"} />
      </Button>
    </Tooltip>
  );
}

function ChatterToggleButton(): ReactElement {
  const t = useUiT();
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
