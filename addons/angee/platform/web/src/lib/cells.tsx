import { Chip, NavLink } from "@angee/ui";
import type { ReactElement } from "react";

const MAX_CHIPS = 6;

/**
 * A wrap of linked chips for a dependency summary (depends-on / depended-by):
 * each chip navigates to a detail page, overflow collapses to a count. A local
 * cell renderer composed from `NavLink` + `Chip` — not a new design-system surface.
 */
export function LinkedChips({
  items,
  href,
  format,
}: {
  items: readonly string[];
  href: (item: string) => string;
  format?: (item: string) => string;
}): ReactElement {
  if (items.length === 0) {
    return <span className="text-fg-muted">—</span>;
  }
  const shown = items.slice(0, MAX_CHIPS);
  const overflow = items.length - shown.length;
  return (
    <span className="flex flex-wrap items-center gap-1">
      {shown.map((item) => (
        <NavLink key={item} href={href(item)} variant="muted">
          <Chip tone="muted" size="sm">{format ? format(item) : item}</Chip>
        </NavLink>
      ))}
      {overflow > 0 ? <Chip tone="muted" size="sm">{`+${overflow}`}</Chip> : null}
    </span>
  );
}
