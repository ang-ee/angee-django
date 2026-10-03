import { createContext, useContext, useId, useState, type ReactElement } from "react";
import { Link, useLinkProps, useRouterState } from "@tanstack/react-router";

import { useHrefLinkOptions } from "../lib/in-app-link";
import { useUiT, type UiTranslate } from "../i18n";
import { toneGlyph } from "../lib/tones";
import { tv } from "../lib/variants";
import { barVariants } from "../layouts/bar";
import { Accordion } from "../ui/accordion";
import { Badge, CountBadge } from "../ui/badge";
import { Tooltip } from "../ui/tooltip";
import { railLinkToggleProps } from "./app-rail-model";
import { useDeveloperRail, type DeveloperRail } from "./DeveloperMode";
import { Glyph } from "./Glyph";
import type { ChromeMenuNode } from "./menu-tree";

const ActiveMenuItemContext = createContext<{ selected?: string; page?: string }>({});
// The tree resolves developer mode once; every item reads it from here.
const DeveloperRailContext = createContext<DeveloperRail | null>(null);
function useRail(): DeveloperRail {
  const rail = useContext(DeveloperRailContext);
  if (!rail) throw new Error("A rail item rendered outside AppRailTree.");
  return rail;
}

export const appRailTreeVariants = tv({
  slots: {
    root: "flex min-w-0 flex-col text-on-rail",
    header: barVariants({
      height: "topbar",
      edge: "bottom",
      tone: "rail",
      pad: "compact",
      gap: 2,
    }),
    title: "min-w-0 flex-1 truncate text-13 font-semibold text-on-rail-hi",
    tree: "flex min-w-0 flex-col px-2 py-1",
    rootItem: "rounded-6",
    row: "group/row flex min-w-0 items-center rounded-6",
    link:
      "flex h-8 min-w-0 flex-1 items-center gap-2 rounded-6 px-2 text-13 text-on-rail-mut no-underline outline-none transition-colors hover:bg-rail-hi hover:text-on-rail-hi focus-visible:focus-ring data-[current=true]:bg-rail-hi data-[current=true]:font-medium data-[current=true]:text-on-rail-hi",
    trigger:
      "grid size-7 shrink-0 place-content-center rounded-6 p-0 text-on-rail-mut outline-none transition-colors hover:bg-rail-hi hover:text-on-rail-hi focus-visible:focus-ring",
    disclosure:
      "transition-transform group-data-[panel-open]:rotate-90 [&_.glyph]:size-3.5",
    panel: "overflow-hidden pb-1 pl-3",
    children:
      "flex min-w-0 flex-col gap-0.5 border-l border-border-on-rail pl-1",
    metadata: "ml-auto flex shrink-0 items-center gap-1",
  },
});

export interface AppRailTreeProps {
  className?: string;
  /** Present one app's included apps without repeating its branded root heading. */
  flat?: boolean;
  /** Which place the tree shows — the apps scope or the Settings swap. */
  scope: "apps" | "settings";
  /** The resolved roots of that place, in display order. */
  roots: readonly ChromeMenuNode[];
  activeRootId: string | null;
  /** Selected ids from the shell's full-tree match; no matching against pruned roots. */
  selectedAppId?: string;
  selectedSubAppId?: string;
  pageId?: string;
  /** Open a requested app without changing which route is marked active. */
  defaultOpenRootId?: string | null;
  /** The rail collapse toggle, fired by a second activation of the current page's link. */
  onActiveToggle?: (() => void) | undefined;
}

/** Accordion navigation rendered as the expanded state of the app rail. */
export function AppRailTree({
  className,
  flat = false,
  scope,
  roots,
  activeRootId,
  selectedAppId,
  selectedSubAppId,
  pageId,
  defaultOpenRootId = activeRootId,
  onActiveToggle,
}: AppRailTreeProps): ReactElement {
  const t = useUiT();
  const rail = useDeveloperRail();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const [openRootId, setOpenRootId] = useDerivedOverride<string | null>(
    defaultOpenRootId,
    `${scope}\0${activeRootId ?? ""}\0${defaultOpenRootId ?? ""}`,
  );
  const idPrefix = `app-rail-${useId().replaceAll(":", "")}`;
  const styles = appRailTreeVariants();
  const [onlyRoot] = roots;

  return (
    <DeveloperRailContext.Provider value={rail}>
    <ActiveMenuItemContext.Provider value={{ selected: selectedSubAppId ?? selectedAppId, page: pageId }}>
      <div className={styles.root({ className })}>
        {scope === "settings" ? (
          <div className={styles.header()}>
            <Link
              to="/"
              aria-label={t("chrome.back")}
              className={styles.trigger()}
            >
              <Glyph name="chevron-left" aria-hidden="true" />
            </Link>
            <h2 className={styles.title()}>{t("chrome.settings")}</h2>
          </div>
        ) : null}
        <div className={styles.tree()}>
          {flat && roots.length === 1 && onlyRoot ? (
            <>
              {rail.apps(onlyRoot).map((item) => (
                <MenuLink key={item.id} item={item}
                  pathname={pathname} styles={styles} onActiveToggle={onActiveToggle} />
              ))}
              <RemovedMenuItems parentId={onlyRoot.id} styles={styles} />
            </>
          ) : <Accordion.Root
            variant="flush"
            value={openRootId ? [openRootId] : []}
            onValueChange={(value) => {
              setOpenRootId(String(value[0] ?? "") || null);
            }}
          >
            {roots.map((item) => (
              <RootMenuItem
                key={item.id}
                idPrefix={idPrefix}
                item={item}
                open={openRootId === item.id}
                pathname={pathname}
                styles={styles}
                onActiveToggle={onActiveToggle}
              />
            ))}
          </Accordion.Root>}
          {scope === "apps" && !flat ? <RemovedMenuItems parentId={null} styles={styles} /> : null}
        </div>
      </div>
    </ActiveMenuItemContext.Provider>
    </DeveloperRailContext.Provider>
  );
}

/** Developer mode: removed apps, struck through where they showed, naming who removed them. */
function RemovedMenuItems({ parentId, styles }: { parentId: string | null; styles: AppRailTreeStyles }): ReactElement | null {
  const t = useUiT();
  // Under a root only removed apps belong to the rail; removed menus show in the top bar.
  const removed = useRail().removedUnder(parentId).filter((node) => parentId === null || node.app);
  if (!removed.length) return null;
  return (
    <>
      {removed.map((node) => (
        <Tooltip key={node.id} label={node.route ? `${node.id} → ${node.route}` : node.id} side="right">
          <span tabIndex={0} role="link" aria-disabled="true" className={`${styles.link()} cursor-default line-through`}>
            <Glyph name="x" size={14} aria-hidden="true" />
            <span className="min-w-0 flex-1 truncate">{t("developer.removedBy", { label: node.displayLabel, layer: node.by })}</span>
          </span>
        </Tooltip>
      ))}
    </>
  );
}

/**
 * A user override that wins until the route-derived default it overrode
 * changes (`key`), at which point the derivation takes back over — the
 * render-time alternative to mirroring route state through effects.
 */
function useDerivedOverride<T>(
  derived: T,
  key: string,
): [T, (next: T) => void] {
  const [override, setOverride] = useState<{ key: string; value: T } | null>(
    null,
  );
  const value = override?.key === key ? override.value : derived;
  return [value, (next) => setOverride({ key, value: next })];
}

type AppRailTreeStyles = ReturnType<typeof appRailTreeVariants>;

function RootMenuItem({
  idPrefix,
  item,
  open,
  pathname,
  styles,
  onActiveToggle,
}: {
  idPrefix: string;
  item: ChromeMenuNode;
  open: boolean;
  pathname: string;
  styles: AppRailTreeStyles;
  onActiveToggle?: (() => void) | undefined;
}): ReactElement | null {
  const t = useUiT();
  const rail = useRail();
  if (!item.target) return null;
  const children = rail.apps(item);
  if (!children.length && !rail.removedUnder(item.id).some((node) => node.app)) {
    return (
      <div className={styles.rootItem()}>
        <MenuLink
          item={item}
          pathname={pathname}
          styles={styles}
          onActiveToggle={onActiveToggle}
        />
      </div>
    );
  }
  const panelId = menuPanelId(idPrefix, item.id);
  const accessibleLabel = menuItemAccessibleLabel(t, item, rail.label(item));
  return (
    <Accordion.Item value={item.id} className={styles.rootItem()}>
      <Accordion.Header className={styles.row()}>
        <MenuLink
          item={item}
          pathname={pathname}
          styles={styles}
          onActiveToggle={onActiveToggle}
        />
        <Accordion.Trigger
          aria-controls={panelId}
          aria-label={t(open ? "chrome.collapseItem" : "chrome.expandItem", {
            label: accessibleLabel,
          })}
          className={styles.trigger()}
        >
          <span className={styles.disclosure()}>
            <Glyph name="chevron-right" aria-hidden="true" />
          </span>
        </Accordion.Trigger>
      </Accordion.Header>
      <Accordion.Panel id={panelId} className={styles.panel()}>
        <MenuChildren
          items={children}
          pathname={pathname}
          styles={styles}
          onActiveToggle={onActiveToggle}
        />
        <RemovedMenuItems parentId={item.id} styles={styles} />
      </Accordion.Panel>
    </Accordion.Item>
  );
}

function MenuChildren({
  items,
  pathname,
  styles,
  onActiveToggle,
}: {
  items: readonly ChromeMenuNode[];
  pathname: string;
  styles: AppRailTreeStyles;
  onActiveToggle?: (() => void) | undefined;
}): ReactElement {
  return (
    <div className={styles.children()}>
      {items.map((item) => (
        <MenuLink
          key={item.id}
          item={item}
          pathname={pathname}
          styles={styles}
          onActiveToggle={onActiveToggle}
        />
      ))}
    </div>
  );
}

function MenuLink({
  item,
  pathname,
  styles,
  onActiveToggle,
}: {
  item: ChromeMenuNode;
  pathname: string;
  styles: AppRailTreeStyles;
  onActiveToggle?: (() => void) | undefined;
}): ReactElement | null {
  const active = useContext(ActiveMenuItemContext);
  const rail = useRail();
  const current = active.selected === item.id;
  const toggleProps = railLinkToggleProps(item.target, pathname, onActiveToggle, true);
  const hrefOptions = useHrefLinkOptions(item.target);
  const linkProps = useLinkProps({
    ...hrefOptions,
    ...toggleProps,
  });
  if (!item.target) return null;
  return (
    <Tooltip label={rail.describe(item)} side="right">
      <a
        {...linkProps}
        aria-current={current ? (active.page === item.id ? "page" : "true") : undefined}
        data-current={current}
        data-status={undefined}
        className={styles.link()}
      >
        <span className={item.tone ? toneGlyph(item.tone) : undefined}>
          <Glyph name={item.iconName} fallbackName="help" size={14} aria-hidden="true" />
        </span>
        <span className="min-w-0 flex-1 truncate">{rail.label(item)}</span>
        <MenuItemMetadata item={item} styles={styles} />
      </a>
    </Tooltip>
  );
}

function MenuItemMetadata({
  item,
  styles,
}: {
  item: ChromeMenuNode;
  styles: AppRailTreeStyles;
}): ReactElement | null {
  const t = useUiT();
  const hasCount = typeof item.badge === "number" && item.badge > 0;
  if (!hasCount && item.status !== "future") return null;
  return (
    <span className={styles.metadata()}>
      {hasCount ? (
        <CountBadge value={item.badge!} tone={item.tone ?? "neutral"} />
      ) : null}
      {item.status === "future" ? (
        <Badge density="micro" tone={item.tone ?? "neutral"}>
          {t("chrome.future")}
        </Badge>
      ) : null}
    </span>
  );
}

function menuItemAccessibleLabel(t: UiTranslate, item: ChromeMenuNode, displayed: string): string {
  let label = displayed;
  if (typeof item.badge === "number" && item.badge > 0) {
    label = t("chrome.itemWithCount", { label, count: item.badge });
  }
  if (item.status === "future") {
    label = t("chrome.futureItem", { label });
  }
  return label;
}

function menuPanelId(prefix: string, itemId: string): string {
  return `${prefix}-${encodeURIComponent(itemId)}-panel`;
}
