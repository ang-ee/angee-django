import {
  useMemo,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";
import { Link, useRouterState } from "@tanstack/react-router";

import { useBaseT } from "../i18n";
import { cn } from "../lib/cn";
import { toneClass as toneFillClass } from "../lib/tones";
import { Button } from "../ui/button";
import {
  PopoverClose,
  PopoverContent,
  PopoverInput,
  PopoverPortal,
  PopoverPositioner,
  type PopoverPositionerProps,
  PopoverRoot,
  PopoverTrigger,
} from "../ui/popover";
import { SectionEyebrow } from "../ui/section-eyebrow";
import { textRoleVariants } from "../ui/text";
import { Tooltip } from "../ui/tooltip";
import { AngeeMark } from "./AngeeMark";
import { Glyph } from "./Glyph";
import {
  type ChromeMenuGroup,
  type ChromeMenuItem,
  type ChromeMenuStatus,
  type ChromeMenuTone,
  MenuTree,
  pathMatchesTarget,
} from "./menu-tree";
import { useChromeMenuItems } from "./refine-menu";

export interface AppChooserItem {
  id: string;
  label: string;
  to: string;
  badge?: number;
  description?: string;
  group?: ChromeMenuGroup;
  icon?: string;
  status?: ChromeMenuStatus;
  tone?: ChromeMenuTone;
}

export interface AppChooserProps {
  activeId?: string;
  align?: PopoverPositionerProps["align"];
  className?: string;
  defaultOpen?: boolean;
  items?: readonly AppChooserItem[];
  searchPlaceholder?: string;
  side?: PopoverPositionerProps["side"];
  sideOffset?: PopoverPositionerProps["sideOffset"];
  title?: ReactNode;
  trigger?: ReactNode;
  triggerLabel?: string;
}

export function AppChooser({
  activeId,
  align = "start",
  className,
  defaultOpen = false,
  items,
  searchPlaceholder,
  side = "right",
  sideOffset = 8,
  title,
  trigger,
  triggerLabel,
}: AppChooserProps): ReactElement {
  const t = useBaseT();
  const resolvedSearchPlaceholder = searchPlaceholder ?? t("chrome.searchApps");
  const resolvedTitle = title ?? t("chrome.switchApp");
  const resolvedTriggerLabel = triggerLabel ?? t("chrome.switchApp");
  const runtimeItems = useChromeMenuItems();
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  });
  const [open, setOpen] = useState(defaultOpen);
  const [query, setQuery] = useState("");
  const resolvedItems = useMemo(
    () => items ?? appChooserItemsFromMenuItems(runtimeItems),
    [items, runtimeItems],
  );
  const visibleItems = useMemo(
    () => filterAppChooserItems(resolvedItems, query),
    [query, resolvedItems],
  );
  const groups = useMemo(() => appChooserGroups(visibleItems), [visibleItems]);
  const currentId =
    activeId ?? resolvedItems.find((item) => pathMatchesTarget(pathname, item.to))?.id;

  return (
    <PopoverRoot open={open} onOpenChange={setOpen}>
      <Tooltip label={resolvedTriggerLabel} side={side}>
        <PopoverTrigger
          aria-label={resolvedTriggerLabel}
          className={cn(
            "group grid size-9 place-content-center rounded-6 text-on-rail-mut outline-none transition-colors hover:bg-rail-hi hover:text-on-rail-hi focus-visible:focus-ring",
            className,
          )}
        >
          {trigger ?? <AngeeMark size={20} aria-hidden="true" />}
        </PopoverTrigger>
      </Tooltip>
      <PopoverPortal>
        <PopoverPositioner side={side} align={align} sideOffset={sideOffset}>
          <PopoverContent
            role="dialog"
            aria-label={resolvedTriggerLabel}
            surface="sheet"
            className="w-[min(45rem,calc(100vw-2rem))] p-5"
          >
            <div className="mb-4 flex items-center gap-3">
              <span className="grid size-7 shrink-0 place-content-center rounded-6 bg-brand-soft text-brand-soft-text">
                <AngeeMark size={18} aria-hidden="true" />
              </span>
              <div className="min-w-0 flex-1">
                <h2 className="m-0 truncate text-15 font-semibold text-fg">
                  {resolvedTitle}
                </h2>
                <p className={cn(textRoleVariants({ role: "caption", truncate: true }), "mt-0.5")}>
                  {t("chrome.appChooserHint")}
                </p>
              </div>
              <PopoverClose
                render={
                  <Button
                    type="button"
                    variant="icon"
                    size="iconSm"
                    aria-label={t("chrome.closeAppChooser")}
                  >
                    <Glyph name="x" />
                  </Button>
                }
              />
            </div>

            <label className="mb-4 flex h-10 items-center gap-2 rounded-8 border border-transparent bg-inset px-4 focus-within:border-border-focus focus-within:focus-ring">
              <Glyph name="search" className="shrink-0 text-fg-muted" />
              <PopoverInput
                type="search"
                value={query}
                onChange={(event) => setQuery(event.currentTarget.value)}
                placeholder={resolvedSearchPlaceholder}
                aria-label={t("chrome.searchAppsLabel")}
                className="min-w-0 flex-1 px-0 text-sm"
              />
            </label>

            <div className="grid gap-4">
              <AppChooserGroup
                title={t("chrome.apps")}
                items={groups.domain}
                activeId={currentId}
                onSelect={() => setOpen(false)}
              />
              <AppChooserGroup
                title={t("chrome.platform")}
                items={groups.platform}
                activeId={currentId}
                onSelect={() => setOpen(false)}
              />
              {visibleItems.length === 0 ? (
                <div className={cn(textRoleVariants({ role: "meta" }), "px-2 py-8 text-center")}>
                  {t("chrome.noApps")}
                </div>
              ) : null}
            </div>
          </PopoverContent>
        </PopoverPositioner>
      </PopoverPortal>
    </PopoverRoot>
  );
}

export function appChooserItemsFromMenuItems(
  items: readonly ChromeMenuItem[],
): readonly AppChooserItem[] {
  const tree = MenuTree.from(items);
  return tree.railMenuItems().flatMap((item) => {
    const target = item.target;
    if (!target) return [];
    return [{
      id: item.id,
      label: item.displayLabel,
      to: target,
      badge: item.badge,
      description: item.description,
      group: item.group,
      icon: item.iconName,
      status: item.status,
      tone: item.tone,
    }];
  });
}

function AppChooserGroup({
  activeId,
  items,
  onSelect,
  title,
}: {
  activeId?: string;
  items: readonly AppChooserItem[];
  onSelect: () => void;
  title: string;
}): ReactElement | null {
  if (!items.length) return null;
  return (
    <section>
      <SectionEyebrow as="h3" tracking="normal" className="mb-2 px-1">
        {title}
      </SectionEyebrow>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        {items.map((item) => (
          <AppChooserTile
            key={item.id}
            item={item}
            active={item.id === activeId}
            onSelect={onSelect}
          />
        ))}
      </div>
    </section>
  );
}

function AppChooserTile({
  active,
  item,
  onSelect,
}: {
  active: boolean;
  item: AppChooserItem;
  onSelect: () => void;
}): ReactElement {
  const disabled = (item.status ?? "active") === "future" || item.to === "#";
  const content = (
    <>
      <span
        className={cn(
          "relative grid size-14 place-content-center rounded-10 text-white shadow-sm [&_.glyph]:size-6",
          toneClass(item.tone),
          active && "ring-2 ring-brand",
        )}
      >
        {item.icon ? (
          <Glyph name={item.icon} />
        ) : (
          <span className="text-base font-semibold">{item.label[0]}</span>
        )}
        {typeof item.badge === "number" && item.badge > 0 ? (
          <span className="absolute right-0 top-0 grid min-w-4 place-content-center rounded-full border-2 border-sheet bg-danger px-1 text-2xs font-semibold text-on-brand">
            {item.badge}
          </span>
        ) : null}
      </span>
      <span className="min-w-0 text-center text-13 font-semibold text-fg">
        {item.label}
      </span>
      {item.description ? (
        <span className={cn(textRoleVariants({ role: "caption" }), "line-clamp-2 text-center leading-[1.125rem]")}>
          {item.description}
        </span>
      ) : null}
    </>
  );

  const className = cn(
    "flex min-h-32 flex-col items-center gap-2 rounded-8 px-3 py-4 text-fg no-underline outline-none transition-colors hover:bg-inset focus-visible:focus-ring",
    disabled && "cursor-not-allowed opacity-50 hover:bg-transparent",
  );

  if (disabled) {
    return (
      <button type="button" className={className} aria-disabled="true">
        {content}
      </button>
    );
  }

  return (
    <Link to={item.to} onClick={onSelect} className={className}>
      {content}
    </Link>
  );
}

function appChooserGroups(items: readonly AppChooserItem[]): {
  domain: readonly AppChooserItem[];
  platform: readonly AppChooserItem[];
} {
  return {
    domain: items.filter((item) => (item.group ?? "domain") === "domain"),
    platform: items.filter((item) => item.group === "platform"),
  };
}

function filterAppChooserItems(
  items: readonly AppChooserItem[],
  query: string,
): readonly AppChooserItem[] {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return items;
  return items.filter((item) => {
    return (
      item.label.toLowerCase().includes(normalized) ||
      item.description?.toLowerCase().includes(normalized)
    );
  });
}

// The primary app tile is a solid brand fill; the rest are soft. Both route
// through the shared (tone × fill) matrix so they can't drift from the palette.
function toneClass(tone: ChromeMenuTone | undefined): string {
  const t = tone ?? "brand";
  return toneFillClass(t, t === "brand" ? "solid" : "soft");
}
