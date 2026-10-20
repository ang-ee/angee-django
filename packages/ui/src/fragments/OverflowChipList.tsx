import * as React from "react";

import { NavLink } from "../ui/nav-link";
import { Chip, type ChipListItem, type ChipSize, type ChipTone } from "../ui/chip";
import { cn } from "../lib/cn";
import { Tooltip } from "../ui/tooltip";

export interface OverflowChipListItem extends ChipListItem {
  /** Makes the chip a link to this destination. */
  href?: string;
}

export interface OverflowChipListProps {
  items: readonly OverflowChipListItem[];
  /** How many chips show before the rest fold into one "+N" chip (default 6). */
  max?: number;
  tone?: ChipTone;
  size?: ChipSize;
  className?: string;
}

/**
 * A bounded wrap of chips: the first `max` items as chips (linked when they carry an
 * `href`), the rest folded into one "+N" chip whose tooltip names them. The one
 * owner of dependency summaries, keyword wraps and template inputs; an empty list
 * renders nothing so the cell owner shows its empty mark.
 * @kind fragment
 * @owner Chip
 */
export function OverflowChipList({ items, max = 6, tone = "muted", size = "sm", className }: OverflowChipListProps): React.ReactElement | null {
  if (items.length === 0) return null;
  const shown = items.slice(0, max);
  const rest = items.slice(max);
  return (
    <span className={cn("inline-flex min-w-0 flex-wrap items-center gap-1", className)}>
      {shown.map((item) => {
        const chip = <Chip tone={tone} size={size} color={item.color}>{item.label}</Chip>;
        return item.href
          ? <NavLink key={item.id} href={item.href} variant="muted">{chip}</NavLink>
          : <React.Fragment key={item.id}>{chip}</React.Fragment>;
      })}
      {rest.length > 0 ? (
        <Tooltip label={rest.map((item) => item.text ?? (typeof item.label === "string" ? item.label : item.id)).join(", ")}>
          <Chip tone={tone} size={size}>{`+${rest.length}`}</Chip>
        </Tooltip>
      ) : null}
    </span>
  );
}
