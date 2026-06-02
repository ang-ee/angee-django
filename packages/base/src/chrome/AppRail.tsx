import type { ReactElement } from "react";
import { Link } from "@tanstack/react-router";
import { useMenus } from "@angee/sdk";
import { CircleHelp } from "lucide-react";

import { cn } from "../lib/cn";
import { Tooltip } from "../ui/tooltip";
import { AppChooser } from "./AppChooser";
import { Glyph } from "./Glyph";
import { useIcon } from "./icon-registry";
import {
  buildMenuTree,
  menuItemIcon,
  menuItemLabel,
  menuItemTarget,
  railMenuItems,
  type ChromeMenuItem,
} from "./menu-tree";

export interface AppRailProps {
  className?: string;
}

const RAIL_BUTTON =
  "group relative grid size-9 place-content-center rounded-6 text-on-rail-mut outline-none transition-colors hover:bg-rail-hi hover:text-on-rail-hi focus-visible:focus-ring";
const RAIL_BUTTON_ACTIVE =
  "bg-rail-hi text-on-rail-hi before:absolute before:-left-[7px] before:top-1/2 before:h-[18px] before:w-[3px] before:-translate-y-1/2 before:rounded-r-2 before:bg-brand before:content-['']";

export function AppRail({ className }: AppRailProps): ReactElement {
  const tree = buildMenuTree(useMenus() as readonly ChromeMenuItem[]);
  const items = railMenuItems(tree);
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

function RailItem({ item }: { item: ChromeMenuItem }): ReactElement | null {
  const iconName = menuItemIcon(item);
  const to = menuItemTarget(item);
  if (!to) return null;
  const label = menuItemLabel(item);
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
        <RailGlyph name={iconName} />
        <span className="sr-only">{label}</span>
      </Link>
    </Tooltip>
  );
}

function RailGlyph({ name }: { name: string }): ReactElement {
  const Icon = useIcon(name);
  if (Icon) return <Glyph name={name} size={16} />;
  return (
    <CircleHelp
      aria-hidden
      className="glyph"
      focusable="false"
      size={16}
      style={{ width: 16, height: 16 }}
    />
  );
}
