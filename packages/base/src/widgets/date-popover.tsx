import type { ReactElement, ReactNode } from "react";
import { isValid, parseISO } from "date-fns";

import { Glyph } from "../chrome/Glyph";
import { Calendar } from "../ui/calendar";
import {
  PopoverContent,
  PopoverPortal,
  PopoverPositioner,
  PopoverRoot,
  PopoverTrigger,
} from "../ui/popover";

/** A date-ish widget value: an ISO string, a `Date`, or empty. */
export type DateWidgetValue = string | Date | null;

/** Parse a widget value to a valid `Date`, or null. */
export function dateFromValue(value: DateWidgetValue | undefined): Date | null {
  if (value instanceof Date) return isValid(value) ? value : null;
  if (!value) return null;
  const parsed = parseISO(value);
  return isValid(parsed) ? parsed : null;
}

/** The raw value as a stable title/string (the ISO form for a `Date`). */
export function valueLabel(value: DateWidgetValue | undefined): string {
  if (value instanceof Date) return value.toISOString();
  return value ?? "";
}

export interface DatePopoverProps {
  /** The selected date highlighted in the calendar (null when unset). */
  selected: Date | null;
  /** Trigger button text (the formatted value or a placeholder). */
  label: ReactNode;
  ariaLabel: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** A calendar day was picked (null when the selected day is deselected). */
  onSelectDate: (date: Date | null) => void;
  /** Rendered under the calendar — a clear button, a time input, etc. */
  footer?: ReactNode;
}

/**
 * The shared date-picker shell: a bordered trigger showing the value, plus a
 * popover holding the single-select `Calendar` and a `footer` slot. The owner of
 * the trigger/popover/calendar chrome the `date` and `datetime` widgets both
 * used; each widget keeps its own value formatting (`onSelectDate`) and footer
 * (clear / time input).
 */
export function DatePopover({
  selected,
  label,
  ariaLabel,
  open,
  onOpenChange,
  onSelectDate,
  footer,
}: DatePopoverProps): ReactElement {
  return (
    <PopoverRoot open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger
        className="inline-flex h-9 w-full min-w-0 items-center justify-between gap-2 rounded border border-border bg-inset px-2 text-left text-13 text-fg outline-none transition-colors hover:border-border-strong focus-visible:border-border-focus focus-visible:focus-ring"
        aria-label={ariaLabel}
      >
        <span className="min-w-0 truncate">{label}</span>
        <Glyph name="calendar" className="shrink-0 text-fg-muted" />
      </PopoverTrigger>
      <PopoverPortal>
        <PopoverPositioner sideOffset={4} align="start">
          <PopoverContent aria-label={ariaLabel} surface="sheet">
            <Calendar
              fixedWeeks
              mode="single"
              selected={selected ?? undefined}
              showOutsideDays
              onSelect={(next) => onSelectDate(next ?? null)}
            />
            {footer}
          </PopoverContent>
        </PopoverPositioner>
      </PopoverPortal>
    </PopoverRoot>
  );
}
