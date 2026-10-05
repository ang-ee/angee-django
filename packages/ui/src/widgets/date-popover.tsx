import type { ReactElement, ReactNode, Ref } from "react";

import { Glyph } from "../chrome/Glyph";
import { Calendar } from "../ui/calendar";
import { inputVariants } from "../ui/input";
import { widgetControlPresentationProps } from "../ui/widget-control";
import type { WidgetControlProps } from "./types";
import {
  PopoverContent,
  PopoverPortal,
  PopoverPositioner,
  PopoverRoot,
  PopoverTrigger,
} from "../ui/popover";
export {
  dateFromValue,
  valueLabel,
  type DateWidgetValue,
} from "./date-format";

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
  triggerRef?: Ref<HTMLButtonElement>;
  controlProps?: WidgetControlProps;
}

/**
 * The shared date-picker layout: a bordered trigger showing the value, plus a
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
  triggerRef,
  controlProps,
}: DatePopoverProps): ReactElement {
  const { presentation, ...triggerProps } = controlProps ?? {};
  const navigationAnchor = selected ?? new Date();
  const startMonth = new Date(navigationAnchor.getFullYear() - 100, 0, 1);
  const endMonth = new Date(navigationAnchor.getFullYear() + 100, 11, 1);
  return (
    <PopoverRoot open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger
        {...triggerProps}
        ref={triggerRef}
        className={inputVariants({
          focus: "visible",
          surface: "inset",
          ...widgetControlPresentationProps(presentation),
          invalid: controlProps?.["aria-invalid"],
          class: "inline-flex min-w-0 items-center justify-between gap-2 text-left",
        })}
        aria-label={ariaLabel}
      >
        <span className="min-w-0 truncate">{label}</span>
        <span data-widget-affordance="" className="shrink-0 text-fg-muted"><Glyph name="calendar" /></span>
      </PopoverTrigger>
      <PopoverPortal>
        <PopoverPositioner sideOffset={4} align="start">
          <PopoverContent aria-label={ariaLabel} surface="sheet">
            <Calendar
              captionLayout="dropdown"
              defaultMonth={navigationAnchor}
              endMonth={endMonth}
              fixedWeeks
              mode="single"
              navLayout="after"
              selected={selected ?? undefined}
              showOutsideDays
              startMonth={startMonth}
              onSelect={(next) => onSelectDate(next ?? null)}
            />
            {footer}
          </PopoverContent>
        </PopoverPositioner>
      </PopoverPortal>
    </PopoverRoot>
  );
}
