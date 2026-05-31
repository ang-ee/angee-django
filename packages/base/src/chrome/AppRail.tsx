import type { ReactElement } from "react";
import { Link } from "@tanstack/react-router";
import { useMenus, type MenuItem } from "@angee/sdk";

import { cn } from "../lib/cn";
import { Tooltip } from "../ui/tooltip";
import { Glyph } from "./Glyph";
import { UserMenu } from "./UserMenu";

export interface AppRailProps {
  className?: string;
}

type MenuWithChrome = MenuItem & {
  children?: readonly MenuWithChrome[];
  icon?: string;
  parent?: string;
  parentId?: string;
};

const RAIL_BUTTON =
  "group relative grid size-9 place-content-center rounded-6 text-on-rail-mut outline-none transition-colors hover:bg-rail-hi hover:text-on-rail-hi focus-visible:focus-ring [&_.glyph]:size-4";
const RAIL_BUTTON_ACTIVE =
  "bg-rail-hi text-on-rail-hi before:absolute before:-left-[7px] before:top-1/2 before:h-[18px] before:w-[3px] before:-translate-y-1/2 before:rounded-r-2 before:bg-brand before:content-['']";

export function AppRail({ className }: AppRailProps): ReactElement {
  const items = railItems(useMenus() as readonly MenuWithChrome[]);
  const home = items[0]?.to ?? items[0]?.children?.find((item) => item.to)?.to ?? "/";
  return (
    <aside
      className={cn(
        "area-rail z-rail flex h-full w-rail-w flex-col items-center gap-2 border-r border-border-on-rail bg-rail py-2 text-on-rail",
        className,
      )}
    >
      <Tooltip label="Angee" side="right">
        <Link
          to={home}
          aria-label="Angee home"
          className={cn(RAIL_BUTTON, "text-on-rail-hi")}
        >
          <Glyph name="angee-cube" size={20} />
        </Link>
      </Tooltip>
      <div className="h-px w-6 bg-border-on-rail" />
      <nav
        aria-label="Primary navigation"
        className="flex min-h-0 flex-1 flex-col items-center gap-1 overflow-y-auto"
      >
        {items.map((item) => (
          <RailItem key={item.id} item={item} />
        ))}
      </nav>
      <UserMenu side="right" align="end" />
    </aside>
  );
}

function RailItem({ item }: { item: MenuWithChrome }): ReactElement | null {
  const to = item.to ?? item.children?.find((child) => child.to)?.to;
  if (!to) return null;
  const label = item.label ?? item.id;
  return (
    <Tooltip label={label} side="right">
      <Link
        to={to}
        aria-label={label}
        className={RAIL_BUTTON}
        activeProps={{
          "aria-current": "page",
          className: RAIL_BUTTON_ACTIVE,
        }}
      >
        <Glyph name={item.icon ?? item.id} />
        <span className="sr-only">{label}</span>
      </Link>
    </Tooltip>
  );
}

function railItems(menus: readonly MenuWithChrome[]): readonly MenuWithChrome[] {
  const childIds = new Set<string>();
  for (const item of menus) {
    for (const child of item.children ?? []) childIds.add(child.id);
    if (item.parent || item.parentId) childIds.add(item.id);
  }
  return menus.filter((item) => {
    if (item.id === "user" || item.id === "systray") return false;
    if (childIds.has(item.id)) return false;
    return Boolean(item.to ?? item.children?.some((child) => child.to));
  });
}
