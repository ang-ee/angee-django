import { useId, useState, type ReactElement } from "react";
import { Link, useRouterState } from "@tanstack/react-router";

import { useUiT, type UiTranslate } from "../i18n";
import { toneGlyph } from "../lib/tones";
import { tv } from "../lib/variants";
import { barVariants } from "../layouts/bar";
import { Accordion } from "../ui/accordion";
import { Badge, CountBadge } from "../ui/badge";
import { Collapsible } from "../ui/collapsible";
import { activeLinkToggleProps } from "./app-rail-model";
import { Glyph } from "./Glyph";
import type { ChromeMenuNode } from "./menu-tree";

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
      "flex h-8 min-w-0 flex-1 items-center gap-2 rounded-6 px-2 text-13 text-on-rail-mut no-underline outline-none transition-colors hover:bg-rail-hi hover:text-on-rail-hi focus-visible:focus-ring data-[active=true]:bg-rail-hi data-[active=true]:font-medium data-[active=true]:text-on-rail-hi",
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
  /** Which place the tree shows — the apps scope or the Settings swap. */
  scope: "apps" | "settings";
  /** The resolved roots of that place, in display order. */
  roots: readonly ChromeMenuNode[];
  activeRootId: string | null;
  /** The rail collapse toggle, fired by a second activation of the current page's link. */
  onActiveToggle?: (() => void) | undefined;
}

/** Accordion navigation rendered as the expanded state of the app rail. */
export function AppRailTree({
  className,
  scope,
  roots,
  activeRootId,
  onActiveToggle,
}: AppRailTreeProps): ReactElement {
  const t = useUiT();
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  });
  const [openRootId, setOpenRootId] = useDerivedOverride<string | null>(
    activeRootId,
    `${scope}\0${activeRootId ?? ""}`,
  );
  const idPrefix = `app-rail-${useId().replaceAll(":", "")}`;
  const styles = appRailTreeVariants();

  return (
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
        <Accordion.Root
          variant="flush"
          value={openRootId ? [openRootId] : []}
          onValueChange={(value) => {
            setOpenRootId(String(value[0] ?? "") || null);
          }}
        >
          {roots.map((item) => (
            <RootMenuItem
              key={item.id}
              active={activeRootId === item.id}
              idPrefix={idPrefix}
              item={item}
              open={openRootId === item.id}
              pathname={pathname}
              styles={styles}
              onActiveToggle={onActiveToggle}
            />
          ))}
        </Accordion.Root>
      </div>
    </div>
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
  active,
  idPrefix,
  item,
  open,
  pathname,
  styles,
  onActiveToggle,
}: {
  active: boolean;
  idPrefix: string;
  item: ChromeMenuNode;
  open: boolean;
  pathname: string;
  styles: AppRailTreeStyles;
  onActiveToggle?: (() => void) | undefined;
}): ReactElement | null {
  const t = useUiT();
  if (!item.target) return null;
  const children = item.targetedChildren;
  if (!children.length) {
    return (
      <div className={styles.rootItem()}>
        <MenuLink
          active={active}
          item={item}
          pathname={pathname}
          styles={styles}
          onActiveToggle={onActiveToggle}
        />
      </div>
    );
  }
  const panelId = menuPanelId(idPrefix, item.id);
  const accessibleLabel = menuItemAccessibleLabel(t, item);
  return (
    <Accordion.Item value={item.id} className={styles.rootItem()}>
      <Accordion.Header className={styles.row()}>
        <MenuLink
          active={active}
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
          idPrefix={idPrefix}
          items={children}
          pathname={pathname}
          styles={styles}
        />
      </Accordion.Panel>
    </Accordion.Item>
  );
}

function MenuChildren({
  idPrefix,
  items,
  pathname,
  styles,
}: {
  idPrefix: string;
  items: readonly ChromeMenuNode[];
  pathname: string;
  styles: AppRailTreeStyles;
}): ReactElement {
  return (
    <div className={styles.children()}>
      {items.map((item) => (
        <NestedMenuItem
          key={item.id}
          idPrefix={idPrefix}
          item={item}
          pathname={pathname}
          styles={styles}
        />
      ))}
    </div>
  );
}

function NestedMenuItem({
  idPrefix,
  item,
  pathname,
  styles,
}: {
  idPrefix: string;
  item: ChromeMenuNode;
  pathname: string;
  styles: AppRailTreeStyles;
}): ReactElement | null {
  const t = useUiT();
  const children = item.targetedChildren;
  const activeChildId = item.activeTargetedChild(pathname)?.id ?? null;
  const [open, setOpen] = useDerivedOverride(
    activeChildId !== null,
    activeChildId ?? "",
  );
  const active = item.matchesPath(pathname);
  if (!item.target) return null;
  if (!children.length) {
    return (
      <MenuLink
        active={active}
        item={item}
        pathname={pathname}
        styles={styles}
      />
    );
  }
  const panelId = menuPanelId(idPrefix, item.id);
  const accessibleLabel = menuItemAccessibleLabel(t, item);
  return (
    <Collapsible.Root
      variant="flush"
      open={open}
      onOpenChange={setOpen}
    >
      <div className={styles.row()}>
        <MenuLink
          active={active}
          item={item}
          pathname={pathname}
          styles={styles}
        />
        <Collapsible.Trigger
          aria-controls={panelId}
          aria-label={t(open ? "chrome.collapseItem" : "chrome.expandItem", {
            label: accessibleLabel,
          })}
          className={styles.trigger()}
        >
          <span className={styles.disclosure()}>
            <Glyph name="chevron-right" aria-hidden="true" />
          </span>
        </Collapsible.Trigger>
      </div>
      <Collapsible.Panel id={panelId} className={styles.panel()}>
        <MenuChildren
          idPrefix={idPrefix}
          items={children}
          pathname={pathname}
          styles={styles}
        />
      </Collapsible.Panel>
    </Collapsible.Root>
  );
}

function MenuLink({
  active,
  item,
  pathname,
  styles,
  onActiveToggle,
}: {
  active: boolean;
  item: ChromeMenuNode;
  pathname: string;
  styles: AppRailTreeStyles;
  onActiveToggle?: (() => void) | undefined;
}): ReactElement | null {
  if (!item.target) return null;
  const current = active && item.activeTargetedChild(pathname) === undefined;
  return (
    <Link
      to={item.target}
      aria-current={current ? "page" : undefined}
      data-active={active}
      {...activeLinkToggleProps(item.target, pathname, onActiveToggle, true)}
      className={styles.link()}
    >
      <span className={item.tone ? toneGlyph(item.tone) : undefined}>
        <Glyph name={item.iconName} fallbackName="help" size={14} aria-hidden="true" />
      </span>
      <span className="min-w-0 flex-1 truncate">{item.displayLabel}</span>
      <MenuItemMetadata item={item} styles={styles} />
    </Link>
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

function menuItemAccessibleLabel(t: UiTranslate, item: ChromeMenuNode): string {
  let label = item.displayLabel;
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
