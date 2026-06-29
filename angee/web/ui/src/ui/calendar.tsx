import * as React from "react";
import {
  DayPicker,
  type ChevronProps,
  type ClassNames as DayPickerClassNames,
  type DayPickerProps,
} from "react-day-picker";

import { Glyph } from "../chrome/Glyph";
import { cn } from "../lib/cn";
import { tv, type VariantProps } from "../lib/variants";

export const calendarVariants = tv({
  slots: {
    root: "relative p-2",
    months: "flex flex-col gap-2",
    month: "space-y-2",
    caption:
      "flex h-7 items-center justify-center px-1 text-13 font-semibold text-fg",
    captionLabel: "text-13 font-semibold text-fg",
    nav:
      "pointer-events-none absolute inset-x-2 top-2 flex items-center justify-between",
    navButton:
      "pointer-events-auto inline-flex size-6 cursor-pointer items-center justify-center rounded-6 text-fg-muted outline-none hover:bg-inset hover:text-fg focus-visible:focus-ring disabled:pointer-events-none disabled:opacity-40",
    chevron: "size-4",
    monthGrid: "w-full border-collapse",
    weekdays: "grid grid-cols-7 gap-0.5 text-2xs tabular-nums",
    weekday:
      "grid h-7 place-content-center text-center font-semibold uppercase text-fg-muted",
    weeks: "grid gap-0.5",
    week: "grid grid-cols-7 gap-0.5",
    day: "grid size-8 place-content-center p-0 text-13 text-fg",
    dayButton:
      "grid size-8 cursor-pointer place-content-center rounded-6 outline-none hover:bg-inset focus-visible:focus-ring",
    selected:
      "[&_button]:bg-brand [&_button]:font-semibold [&_button]:text-on-brand [&_button]:hover:bg-brand-hover",
    today: "[&_button]:font-semibold [&_button]:text-brand-soft-text",
    outside: "[&_button]:text-fg-subtle [&_button]:opacity-50",
    disabled: "[&_button]:cursor-not-allowed [&_button]:text-fg-subtle",
    rangeStart: "[&_button]:rounded-r-none",
    rangeMiddle:
      "[&_button]:rounded-none [&_button]:bg-brand-soft [&_button]:text-brand-soft-text",
    rangeEnd: "[&_button]:rounded-l-none",
  },
  variants: {
    size: {
      sm: {
        weekday: "h-btn-sm",
        day: "size-btn-sm text-xs",
        dayButton: "size-btn-sm",
      },
      md: "",
    },
  },
  defaultVariants: {
    size: "md",
  },
});

export type CalendarRecipeProps = VariantProps<typeof calendarVariants>;
export type CalendarSize = NonNullable<CalendarRecipeProps["size"]>;
export type CalendarClassNames = DayPickerClassNames;
export type CalendarProps = DayPickerProps &
  CalendarRecipeProps & {
    classNames?: Partial<CalendarClassNames>;
  };

export function calendarClassNames(
  props?: CalendarRecipeProps,
): Partial<CalendarClassNames> {
  const styles = calendarVariants(props);
  return {
    root: styles.root(),
    months: styles.months(),
    month: styles.month(),
    month_caption: styles.caption(),
    caption_label: styles.captionLabel(),
    nav: styles.nav(),
    button_previous: styles.navButton(),
    button_next: styles.navButton(),
    chevron: styles.chevron(),
    month_grid: styles.monthGrid(),
    weekdays: styles.weekdays(),
    weekday: styles.weekday(),
    weeks: styles.weeks(),
    week: styles.week(),
    day: styles.day(),
    day_button: styles.dayButton(),
    selected: styles.selected(),
    today: styles.today(),
    outside: styles.outside(),
    disabled: styles.disabled(),
    range_start: styles.rangeStart(),
    range_middle: styles.rangeMiddle(),
    range_end: styles.rangeEnd(),
  };
}

function mergeClassNames(
  base: Partial<CalendarClassNames>,
  overrides?: Partial<CalendarClassNames>,
): Partial<CalendarClassNames> {
  if (!overrides) return base;

  const merged = { ...base };
  for (const [key, value] of Object.entries(overrides)) {
    const classNameKey = key as keyof CalendarClassNames;
    merged[classNameKey] = cn(base[classNameKey], value);
  }
  return merged;
}

function CalendarChevron({
  className,
  orientation = "right",
  size = 16,
  style,
}: ChevronProps): React.ReactElement {
  const iconName =
    orientation === "left"
      ? "chevron-left"
      : orientation === "up"
        ? "chevron-up"
        : orientation === "down"
          ? "chevron-down"
          : "chevron-right";

  return (
    <span
      aria-hidden="true"
      className={cn("inline-flex items-center justify-center", className)}
      style={style}
    >
      <Glyph name={iconName} size={size} />
    </span>
  );
}

export function Calendar({
  classNames,
  components,
  size = "md",
  ...props
}: CalendarProps): React.ReactElement {
  return (
    <DayPicker
      {...props}
      classNames={mergeClassNames(calendarClassNames({ size }), classNames)}
      components={{ Chevron: CalendarChevron, ...components }}
    />
  );
}
