import type { ReactElement } from "react";

import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { useChatter } from "../communication/chatter-context";
import { barVariants } from "../layouts/bar";
import { Button } from "../ui/button";
import { Tooltip } from "../ui/tooltip";
import { CommandPalette } from "./CommandPalette";
import { AppMenu } from "./AppMenu";
import { useShellRegion } from "./shell-containers";
import { Glyph } from "./Glyph";
import { UserMenu } from "./UserMenu";
import { DeveloperMenu } from "./DeveloperMode";

/**
 * The console's top bar. Its contents are the shell's own: addons reach it
 * through the shell containers (`shell#regions`, `shell#user-menu`), not props.
 */
export interface TopBarProps {
  navigation?: {
    open: boolean;
    toggle: () => void;
  };
  primaryPane?: {
    collapsed: boolean;
    toggle: () => void;
  };
  chatterPane?: {
    collapsed: boolean;
    toggle: () => void;
  };
  showChatterToggle?: boolean;
  showUserMenu?: boolean;
  className?: string;
}

export function TopBar({
  navigation,
  primaryPane,
  chatterPane,
  showChatterToggle = false,
  showUserMenu = false,
  className,
}: TopBarProps): ReactElement {
  const t = useUiT();
  const showAppMenu = useShellRegion("chrome.app-menu");
  return (
    <header
      aria-label={t("chrome.topBar")}
      className={cn(
        barVariants({ height: "topbar", edge: "bottom", tone: "rail", gap: 3 }),
        // Grid placement + stacking, plus TopBar's asymmetric leading pad.
        "area-topbar z-topbar gap-2 px-2 sm:gap-3 sm:px-3 sm:pl-4",
        className,
      )}
    >
      {navigation ? <NavigationToggleButton navigation={navigation} /> : null}
      {primaryPane ? <PrimaryPaneToggleButton pane={primaryPane} /> : null}
      {showAppMenu ? <AppMenu /> : null}
      <div className="min-w-2 flex-1" />
      <CommandPalette />
      <DeveloperMenu />
      {showUserMenu ? (
        <UserMenu
          className="size-icon-btn-md rounded-6 border-0"
          side="bottom"
          align="end"
          sideOffset={6}
        />
      ) : null}
      {showChatterToggle ? <ChatterToggleButton pane={chatterPane} /> : null}
    </header>
  );
}

function NavigationToggleButton({
  navigation,
}: {
  navigation: NonNullable<TopBarProps["navigation"]>;
}): ReactElement {
  const t = useUiT();
  return (
    <Tooltip label={t("chrome.primaryNav")}>
      <Button
        type="button"
        variant="icon"
        size="iconSm"
        active={navigation.open}
        aria-label={t("chrome.primaryNav")}
        aria-expanded={navigation.open}
        onClick={navigation.toggle}
        className="text-on-rail-mut hover:bg-rail-hi hover:text-on-rail-hi"
      >
        <Glyph name="app-rail" />
      </Button>
    </Tooltip>
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
        aria-expanded={open}
        onClick={pane.toggle}
        className="text-on-rail-mut hover:bg-rail-hi hover:text-on-rail-hi"
      >
        <Glyph name="panel-left" />
      </Button>
    </Tooltip>
  );
}

function ChatterToggleButton({
  pane,
}: {
  pane?: NonNullable<TopBarProps["chatterPane"]>;
}): ReactElement {
  const t = useUiT();
  const { collapsed, toggleCollapsed } = useChatter();
  const effectivePane = pane ?? { collapsed, toggle: toggleCollapsed };
  const open = !effectivePane.collapsed;
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
        aria-expanded={open}
        onClick={effectivePane.toggle}
        className="text-on-rail-mut hover:bg-rail-hi hover:text-on-rail-hi"
      >
        <Glyph name="panel-right" />
      </Button>
    </Tooltip>
  );
}
