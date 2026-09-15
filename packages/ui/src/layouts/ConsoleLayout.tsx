import * as React from "react";
import { useRouterState } from "@tanstack/react-router";

import { AppRail } from "../chrome/AppRail";
import { BreadcrumbLabelProvider } from "../chrome/Breadcrumb";
import { DrawerRail } from "../chrome/DrawerRail";
import { TopBar } from "../chrome/TopBar";
import { Chatter } from "../communication/Chatter";
import { ChatterProvider, useChatter, type ChatterPaneController } from "../communication/chatter-context";
import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { SlotOutlet } from "../lib/slot-outlet";
import {
  LARGE_VIEWPORT_QUERY,
  MOBILE_VIEWPORT_QUERY,
  useMediaQuery,
} from "../lib/use-media-query";
import type { CollapsiblePane } from "../page";
import { useSlot } from "../runtime";
import { Drawer } from "../ui/drawer";
import { ControlBandProvider } from "./ControlBand";
import { DrawerProvider } from "./drawer-context";
import { DrawerOverlay } from "./DrawerOverlay";
import { PrimaryPaneProvider, usePrimaryPaneContent } from "./primary-pane-context";
import { StatuslineProvider } from "./Statusline";
import { Workbench } from "./Workbench";

type PaneToggleController = Pick<CollapsiblePane, "collapsed" | "toggle">;

/** Additive notices below console navigation and above the active page controls. */
export const CONSOLE_NOTICE_SLOT = "console.notice";

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
  const notices = useSlot(CONSOLE_NOTICE_SLOT);
  const [controlHost, setControlHost] =
    React.useState<HTMLDivElement | null>(null);
  const [statusHost, setStatusHost] =
    React.useState<HTMLDivElement | null>(null);
  const [primaryController, setPrimaryController] =
    React.useState<PaneToggleController | null>(null);
  const [compactChatterController, setCompactChatterController] =
    React.useState<PaneToggleController | null>(null);
  const [railWidth, setRailWidth] = React.useState<string | null>(null);
  const [navigationOpen, setNavigationOpen] = React.useState(false);
  const mobileViewport = useMediaQuery(MOBILE_VIEWPORT_QUERY);
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  });
  React.useEffect(() => setNavigationOpen(false), [pathname]);
  React.useEffect(() => {
    if (!mobileViewport) setNavigationOpen(false);
  }, [mobileViewport]);
  const handlePrimaryController = React.useCallback(
    (controller: PaneToggleController | null) => {
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
    <ChatterProvider defaultCollapsed>
      <PrimaryPaneProvider>
        <DrawerProvider>
          <ControlBandProvider host={controlHost}>
            <StatuslineProvider host={statusHost}>
              <BreadcrumbLabelProvider>
                <div
                  style={{
                    "--rail-current-w": mobileViewport
                      ? "0px"
                      : railWidth ?? "var(--spacing-rail-w)",
                  } as React.CSSProperties}
                  className={cn(
                    "console-grid h-dvh min-h-0 w-full min-w-0 max-w-full overflow-hidden bg-canvas text-fg",
                    className,
                  )}
                >
                  {mobileViewport ? null : (
                    <AppRail onWidthChange={setRailWidth} />
                  )}
                  <TopBar
                    className="area-topbar"
                    navigation={mobileViewport ? {
                      open: navigationOpen,
                      toggle: () => {
                        if (primaryController && !primaryController.collapsed) {
                          primaryController.toggle();
                        }
                        if (
                          compactChatterController
                          && !compactChatterController.collapsed
                        ) {
                          compactChatterController.toggle();
                        }
                        setNavigationOpen((open) => !open);
                      },
                    } : undefined}
                    primaryPane={
                      primaryController
                        ? {
                            collapsed: primaryController.collapsed,
                            toggle: () => {
                              setNavigationOpen(false);
                              primaryController.toggle();
                            },
                          }
                        : undefined
                    }
                    chatterPane={compactChatterController ? {
                      collapsed: compactChatterController.collapsed,
                      toggle: () => {
                        setNavigationOpen(false);
                        compactChatterController.toggle();
                      },
                    } : undefined}
                    showChatterToggle={showChatter}
                    showUserMenu
                  />
                  <div className="area-control min-w-0">
                    <div className="contents" data-console-notices>
                      <SlotOutlet entries={notices} />
                    </div>
                    <div
                      ref={setControlHost}
                      className="contents"
                      data-console-controls
                    />
                  </div>
                  <ConsoleWorkbench
                    showChatter={showChatter}
                    onPrimaryController={handlePrimaryController}
                    onCompactChatterController={setCompactChatterController}
                  >
                    {children}
                  </ConsoleWorkbench>
                  {/* Optional statusline; the row collapses while this host is empty. */}
                  <div
                    ref={setStatusHost}
                    className="area-status console-statusline-host"
                  />
                </div>
                <Drawer.Root
                  open={mobileViewport && navigationOpen}
                  onOpenChange={setNavigationOpen}
                >
                  <Drawer.Portal>
                    <Drawer.Backdrop />
                    <Drawer.Content
                      side="left"
                      aria-label="Primary navigation"
                      className="w-[min(20rem,calc(100vw-2rem))] border-0 bg-rail p-0"
                    >
                      <AppRail presentation="drawer" />
                    </Drawer.Content>
                  </Drawer.Portal>
                </Drawer.Root>
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
 * as content, and Chatter or a record preview as the (collapsible) secondary pane. Lives inside
 * `ChatterProvider` so it can register the secondary pane's collapse controller
 * with the shell bridge. The primary pane's controller is surfaced up to
 * `ConsoleLayout` so the TopBar's left-panel toggle drives it too.
 *
 * The primary pane exists only while a page publishes a contextual explorer.
 */
function ConsoleWorkbench({
  showChatter,
  onPrimaryController,
  onCompactChatterController,
  children,
}: {
  showChatter: boolean;
  onPrimaryController: (controller: PaneToggleController | null) => void;
  onCompactChatterController: (controller: PaneToggleController | null) => void;
  children: React.ReactNode;
}): React.ReactElement {
  const t = useUiT();
  const { recordSupportKey, recordPreview, registerSecondaryController, setCollapsed } = useChatter();
  const { node: publishedPrimary } = usePrimaryPaneContent();
  const largeViewport = useMediaQuery(LARGE_VIEWPORT_QUERY);
  const [desktopPrimaryController, setDesktopPrimaryController] =
    React.useState<CollapsiblePane | null>(null);
  const [compactPrimaryOpen, setCompactPrimaryOpen] = React.useState(false);
  const [compactChatterOpen, setCompactChatterOpen] = React.useState(false);
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  });
  const compactAsideAvailable = showChatter || recordSupportKey !== null;
  const desktopChatter = showChatter && largeViewport && recordSupportKey === null;
  const desktopPreview = largeViewport && recordSupportKey !== null ? recordPreview : null;
  const desktopPrimary = largeViewport ? publishedPrimary : null;
  const compactPrimary = !largeViewport ? publishedPrimary : null;
  const toggleCompactPrimary = React.useCallback(() => {
    setCompactChatterOpen(false);
    setCompactPrimaryOpen((open) => !open);
  }, []);
  const compactPrimaryController = React.useMemo<PaneToggleController>(
    () => ({
      collapsed: !compactPrimaryOpen,
      toggle: toggleCompactPrimary,
    }),
    [compactPrimaryOpen, toggleCompactPrimary],
  );
  const effectivePrimaryController = publishedPrimary == null
    ? null
    : largeViewport
      ? desktopPrimaryController
      : compactPrimaryController;
  const toggleCompactChatter = React.useCallback(() => {
    setCompactPrimaryOpen(false);
    setCompactChatterOpen((open) => !open);
  }, []);
  const compactChatterController = React.useMemo<ChatterPaneController>(
    () => ({
      collapsed: !compactChatterOpen,
      collapse: () => setCompactChatterOpen(false),
      expand: () => { setCompactPrimaryOpen(false); setCompactChatterOpen(true); },
      toggle: toggleCompactChatter,
    }),
    [compactChatterOpen, toggleCompactChatter],
  );
  React.useEffect(() => {
    onPrimaryController(effectivePrimaryController);
    return () => onPrimaryController(null);
  }, [effectivePrimaryController, onPrimaryController]);
  React.useEffect(() => {
    if (desktopPreview) setCollapsed(false);
  }, [desktopPreview, setCollapsed]);
  React.useLayoutEffect(() => {
    setCompactPrimaryOpen(false);
    setCompactChatterOpen(false);
  }, [pathname, largeViewport]);
  React.useLayoutEffect(() => {
    if (largeViewport || !recordPreview) return;
    setCompactPrimaryOpen(false);
    setCompactChatterOpen(true);
  }, [largeViewport, recordPreview]);
  React.useLayoutEffect(() => {
    if (!compactAsideAvailable || largeViewport) return;
    registerSecondaryController(compactChatterController);
    return () => registerSecondaryController(null);
  }, [compactAsideAvailable, compactChatterController, largeViewport, registerSecondaryController]);
  React.useEffect(() => {
    const controller = compactAsideAvailable && !largeViewport
      ? compactChatterController
      : null;
    onCompactChatterController(controller);
    return () => onCompactChatterController(null);
  }, [
    compactChatterController,
    compactAsideAvailable,
    largeViewport,
    onCompactChatterController,
  ]);
  return (
    <>
      <Workbench
        className="area-content"
        autoSave="console.workbench.v2"
        scrollMode="contained"
        secondaryDefaultCollapsed
        primary={
          desktopPrimary != null ? (
            <ControlBandProvider host={undefined}>
              {desktopPrimary}
            </ControlBandProvider>
          ) : undefined
        }
        secondary={desktopPreview ? (
          <ControlBandProvider host={undefined}>
            {desktopPreview}
          </ControlBandProvider>
        ) : desktopChatter ? (
          <ControlBandProvider host={undefined}>
            <Chatter />
          </ControlBandProvider>
        ) : undefined}
        onPrimaryController={setDesktopPrimaryController}
        onSecondaryController={desktopChatter || desktopPreview ? registerSecondaryController : undefined}
      >
        <main className="console-content-main">{children}</main>
      </Workbench>
      <Drawer.Root
        open={compactPrimary != null && compactPrimaryOpen}
        onOpenChange={setCompactPrimaryOpen}
      >
        <Drawer.Portal>
          <Drawer.Backdrop />
          <Drawer.Content
            side="left"
            aria-label={t("chrome.primaryPane")}
            className="w-[min(24rem,calc(100vw-1rem))] p-0"
          >
            <ControlBandProvider host={undefined}>
              {compactPrimary}
            </ControlBandProvider>
          </Drawer.Content>
        </Drawer.Portal>
      </Drawer.Root>
      {!largeViewport && (recordSupportKey === null || recordPreview) ? <Drawer.Root
        open={compactAsideAvailable && compactChatterOpen}
        onOpenChange={setCompactChatterOpen}
      >
        <Drawer.Portal keepMounted>
          <Drawer.Backdrop />
          <Drawer.Content
            side="right"
            aria-label={recordSupportKey === null ? "Chatter" : t("chatter.tabRecords")}
            className="w-[min(28rem,calc(100vw-1rem))] p-0"
          >
            <ControlBandProvider host={undefined}>
              {recordSupportKey === null ? <Chatter /> : recordPreview}
            </ControlBandProvider>
          </Drawer.Content>
        </Drawer.Portal>
      </Drawer.Root> : null}
    </>
  );
}
