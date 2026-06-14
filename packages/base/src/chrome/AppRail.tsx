import type { ReactElement } from "react";
import { Link } from "@tanstack/react-router";
import { useMenus } from "@angee/sdk";

import { cn } from "../lib/cn";
import { Tooltip } from "../ui/tooltip";
import { AppChooser } from "./AppChooser";
import { Glyph } from "./Glyph";
import {
  type ChromeMenuItem,
  type ChromeMenuNode,
  MenuTree,
} from "./menu-tree";

export interface AppRailProps {
  className?: string;
}

const RAIL_BUTTON =
  "group relative grid size-9 place-content-center rounded-6 text-on-rail-mut outline-none transition-colors hover:bg-rail-hi hover:text-on-rail-hi focus-visible:focus-ring";
const RAIL_BUTTON_ACTIVE =
  "bg-rail-hi text-on-rail-hi before:absolute before:-left-[7px] before:top-1/2 before:h-[18px] before:w-[3px] before:-translate-y-1/2 before:rounded-r-2 before:bg-brand before:content-['']";

export function AppRail({ className }: AppRailProps): ReactElement {
  const tree = MenuTree.from(useMenus() as readonly ChromeMenuItem[]);
  const items = tree.railMenuItems();
  return (
    <aside
      className={cn(
        "area-rail z-rail flex h-full w-rail-w flex-col items-center gap-2 border-r border-border-on-rail bg-rail py-2 text-on-rail",
        className,
      )}
    >
      <AppChooser className="text-on-rail-hi" />
      <div className="h-px w-6 bg-border-on-rail" />
      <nav
        aria-label="Primary navigation"
        className="flex min-h-0 flex-1 flex-col items-center gap-1 overflow-y-auto"
      >
        {items.map((item) => (
          <RailItem key={item.id} item={item} />
        ))}
      </nav>
    </aside>
  );
}

function RailItem({ item }: { item: ChromeMenuNode }): ReactElement | null {
  const iconName = item.iconName;
  const to = item.target;
  if (!to) return null;
  const label = item.displayLabel;
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
        <Glyph name={iconName} fallbackName="help" size={16} />
        <span className="sr-only">{label}</span>
      </Link>
    </Tooltip>
  );
}
