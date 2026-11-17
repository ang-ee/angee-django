import * as React from "react";

import { useUiT } from "../i18n";
import { tv, type VariantProps } from "../lib/variants";
import { dateFromUnknown, formatDate, formatDateParts } from "../widgets/date-format";

/**
 * @kind token
 */
export const dateTileVariants = tv({
  slots: {
    root: "inline-grid shrink-0 content-center justify-items-center rounded-6 bg-sheet text-center text-fg",
    month: "text-fg-muted",
    day: "font-display tabular-nums",
    pending: "w-min text-fg-muted",
  },
  variants: {
    // The size sets the type step after the line-height so the recipe merge keeps both.
    size: {
      sm: { root: "min-w-10 gap-0.5 px-1.5 py-1", month: "text-2xs leading-tight", day: "text-lg leading-none", pending: "text-2xs leading-tight" },
      md: { root: "min-w-12 gap-0.5 px-2 py-1.5", month: "text-2xs leading-tight", day: "text-22 leading-none", pending: "text-2xs leading-tight" },
      lg: { root: "min-w-14 gap-1 px-2.5 py-2", month: "text-12 leading-tight", day: "text-28 leading-none", pending: "text-12 leading-tight" },
    },
  },
  defaultVariants: {
    size: "md",
  },
});

export type DateTileProps = Omit<React.HTMLAttributes<HTMLElement>, "children" | "className"> &
  VariantProps<typeof dateTileVariants> & {
    className?: string;
    /** The date; empty or invalid shows the pending state. */
    value: Date | string | number | null | undefined;
    /** Calendar time zone for the month and day. */
    timeZone?: string;
    /** Replaces the default pending copy. */
    pendingLabel?: React.ReactNode;
  };

/**
 * A date as a tile: the short month over a large day, in the display face. A
 * missing date shows the pending state instead. The full date is the tooltip and
 * what assistive technology reads.
 * @kind fragment
 */
export function DateTile({
  className,
  pendingLabel,
  size,
  timeZone,
  value,
  ...props
}: DateTileProps): React.ReactElement {
  const t = useUiT();
  const styles = dateTileVariants({ size });
  const date = dateFromUnknown(value);
  const options = timeZone ? { timeZone } : {};
  const parts = date ? formatDateParts(date, options) : null;
  if (!date || !parts) {
    return (
      <span data-pending="" className={styles.root({ className })} {...props}>
        <span className={styles.pending()}>{pendingLabel ?? t("dateTile.pending")}</span>
      </span>
    );
  }
  const full = formatDate(date, { ...options, density: "full" });
  return (
    <time dateTime={date.toISOString()} title={full} className={styles.root({ className })} {...props}>
      <span aria-hidden className={styles.month()}>{parts.month}</span>
      <span aria-hidden className={styles.day()}>{parts.day}</span>
      <span className="sr-only">{full}</span>
    </time>
  );
}
