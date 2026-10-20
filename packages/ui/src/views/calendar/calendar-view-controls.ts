import { addDays, addMonths, addWeeks, format, startOfWeek } from "date-fns";

import type { UiTranslate } from "../../i18n";
import { formatDate, formatMonth } from "../../widgets/date-format";
import {
  CALENDAR_ANCHOR_FORMAT,
  CALENDAR_VIEW_MODES,
  type CalendarViewMode,
} from "../resource/resource-view-model";

// The calendar kind's period-nav math and labels, derived from mode + anchor —
// no FullCalendar imperative API. Period titles come from the shared date owner,
// so a period reads the same as the resource-view date-group labels.

/** Parse a `yyyy-MM-dd` anchor into a local `Date` (a bare day never crosses UTC). */
export function calendarAnchorToDate(anchor: string): Date {
  const [year, month, day] = anchor.split("-").map(Number);
  if (year && month && day) return new Date(year, month - 1, day);
  return new Date();
}

/** Serialize a `Date` back to the `yyyy-MM-dd` anchor. */
export function calendarDateToAnchor(date: Date): string {
  return format(date, CALENDAR_ANCHOR_FORMAT);
}

const STEP_BY_MODE: Record<CalendarViewMode, (date: Date, amount: number) => Date> = {
  month: addMonths,
  week: addWeeks,
  day: addDays,
};

/** The anchor one period earlier (`-1`) or later (`+1`) for the active mode. */
export function shiftCalendarAnchor(
  mode: CalendarViewMode,
  anchor: string,
  direction: 1 | -1,
): string {
  return calendarDateToAnchor(STEP_BY_MODE[mode](calendarAnchorToDate(anchor), direction));
}

/** The current-period title for the active mode + anchor. */
export function calendarPeriodTitle(
  mode: CalendarViewMode,
  anchor: string,
  t: UiTranslate,
): string {
  const date = calendarAnchorToDate(anchor);
  if (mode === "month") return formatMonth(date);
  if (mode === "week") {
    return t("calendar.weekOf", { date: formatDate(startOfWeek(date), { density: "full" }) });
  }
  return formatDate(date, { density: "full", weekday: true });
}

/** The mode-switch segmented options, labelled through the ui translator. */
export function calendarModeOptions(
  t: UiTranslate,
): readonly { value: CalendarViewMode; label: string }[] {
  return CALENDAR_VIEW_MODES.map((mode) => ({
    value: mode,
    label: t(`calendar.mode.${mode}`),
  }));
}
