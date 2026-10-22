/**
 * The one owner of human dates and times. The rule: every list, row, facts line
 * and day label is absolute (`formatDate`, `formatDateTime`, `formatTime`,
 * `formatDayLabel`, `formatMonth`); relative time (`formatRelativeTime`,
 * `RelativeTime`) belongs only to a live header — a chat, a running job — whose
 * value keeps moving while the page is open. Never `toLocale*`, never a date-fns
 * `format` with a literal pattern, never a raw ISO string as user text.
 */
import { format, formatDistance, isDate, isValid, parseISO, type Locale } from "date-fns";
import { getHumanLocale } from "../lib/human-locale";

export { setHumanDateLocale } from "../lib/human-locale";

/** A date-ish widget value: an ISO string, a `Date`, or empty. */
export type DateWidgetValue = string | Date | null;
export type DateFormatValue = DateWidgetValue | number | undefined;

/**
 * @kind null
 * @reason Format utility: converts data to display or storage text.
 */
export const DATE_STORAGE_FORMAT = "yyyy-MM-dd";
/**
 * @kind null
 * @reason Format utility: converts data to display or storage text.
 */
export const DATETIME_STORAGE_FORMAT = "yyyy-MM-dd'T'HH:mm";
/**
 * @kind null
 * @reason Format utility: converts data to display or storage text.
 */
export const TIME_INPUT_FORMAT = "HH:mm";

/** Parse a widget value to a valid `Date`, or null for empty/invalid input.
 * @kind null
 * @reason Format utility: converts data to display or storage text.
 */
export function dateFromValue(value: DateFormatValue): Date | null {
  if (isDate(value)) return isValid(value) ? value : null;
  if (typeof value === "number") {
    const date = new Date(value);
    return isValid(date) ? date : null;
  }
  if (!value) return null;
  const parsed = parseISO(value);
  return isValid(parsed) ? parsed : null;
}

/**
 * Parse any row/display value to a valid `Date`, or null for non-date values.
 * String values use ISO-8601 by design.
 */
export function dateFromUnknown(value: unknown): Date | null {
  if (isDate(value) || typeof value === "number" || typeof value === "string") {
    return dateFromValue(value);
  }
  return null;
}

/** The raw value as a stable title/string (the ISO form for a `Date`).
 * @kind null
 * @reason Format utility: converts data to display or storage text.
 */
export function valueLabel(value: DateFormatValue): string {
  if (isDate(value)) return isValid(value) ? value.toISOString() : "";
  if (typeof value === "number") return String(value);
  return value ?? "";
}

export interface HumanDateOptions {
  /** App language tag, or a date-fns locale when its distance strings are needed. */
  locale?: string | Locale;
  /** Calendar time zone for a date label, including its current-year decision. */
  timeZone?: string;
  /** Reference date for year-sensitive or relative labels. */
  now?: Date;
  /** List cells omit the current year; full labels retain it for record reads and hover. */
  density?: "list" | "full";
  /** Lead with the weekday ("Monday, Sep 28, 2026"): a period title, never a cell. */
  weekday?: boolean;
}

function localeCode(locale?: string | Locale): string {
  return typeof locale === "string" ? locale : locale?.code ?? getHumanLocale();
}

/** Compact calendar date, including the year only outside the current year.
 * @kind null
 * @reason Format utility: converts data to display or storage text.
 */
export function formatDate(value: DateFormatValue, options: HumanDateOptions = {}): string {
  const date = dateFromValue(value);
  if (!date) return "";
  const now = options.now ?? new Date();
  const year = options.timeZone
    ? (value: Date) => new Intl.DateTimeFormat("en", { year: "numeric", timeZone: options.timeZone }).format(value)
    : (value: Date) => String(value.getFullYear());
  return new Intl.DateTimeFormat(localeCode(options.locale), {
    ...(options.weekday ? { weekday: "long" } : {}),
    month: "short",
    day: "numeric",
    ...(options.density === "full" || year(date) !== year(now) ? { year: "numeric" } : {}),
    ...(options.timeZone ? { timeZone: options.timeZone } : {}),
  }).format(date);
}

/** Compact endpoints separated by an en dash; callers may add a time to each endpoint.
 * @kind null
 * @reason Format utility: converts data to display or storage text.
 */
export function formatDateRange(
  start: DateFormatValue,
  end: DateFormatValue,
  options: HumanDateOptions & { formatEndpoint?: (date: Date, label: string) => string } = {},
): string {
  const context = { ...options, now: options.now ?? new Date() };
  const startDate = dateFromValue(start);
  const endDate = dateFromValue(end);
  const firstLabel = formatDate(startDate, context);
  const lastLabel = formatDate(endDate, context);
  const first = firstLabel && startDate ? options.formatEndpoint?.(startDate, firstLabel) ?? firstLabel : "";
  const last = lastLabel && endDate ? options.formatEndpoint?.(endDate, lastLabel) ?? lastLabel : "";
  return first && last ? `${first} – ${last}` : first || last;
}

/** Human elapsed time using date-fns distance for its locales and Intl for app language tags.
 * @kind null
 * @reason Format utility: converts data to display or storage text.
 */
export function formatRelativeTime(
  value: DateFormatValue,
  options: HumanDateOptions & { addSuffix?: boolean } = {},
): string {
  const date = dateFromValue(value);
  if (!date) return "";
  const now = options.now ?? new Date();
  const locale = localeCode(options.locale);
  if (typeof options.locale === "object" || locale.split("-")[0] === "en") {
    return formatDistance(date, now, {
      addSuffix: options.addSuffix ?? true,
      locale: typeof options.locale === "object" ? options.locale : undefined,
    });
  }
  const minutes = Math.round((date.getTime() - now.getTime()) / 60_000);
  const magnitude = Math.abs(minutes);
  const [unit, amount]: [Intl.RelativeTimeFormatUnit, number] = magnitude < 45
    ? ["minute", minutes]
    : magnitude < 1_320 ? ["hour", Math.round(minutes / 60)]
      : magnitude < 37_440 ? ["day", Math.round(minutes / 1_440)]
        : magnitude < 525_600 ? ["month", Math.round(minutes / 43_200)]
          : ["year", Math.round(minutes / 525_600)];
  if (options.addSuffix === false) {
    return new Intl.NumberFormat(locale, {
      style: "unit", unit, unitDisplay: "long",
    }).format(Math.abs(amount));
  }
  return new Intl.RelativeTimeFormat(locale, {
    numeric: "auto", style: "long",
  }).format(amount, unit);
}

/** Clock time without seconds ("1:45 PM", "13:45"), for a row whose day a label already names.
 * @kind null
 * @reason Format utility: converts data to display or storage text.
 */
export function formatTime(value: DateFormatValue, options: HumanDateOptions = {}): string {
  const date = dateFromValue(value);
  if (!date) return "";
  return new Intl.DateTimeFormat(localeCode(options.locale), {
    timeStyle: "short",
    ...(options.timeZone ? { timeZone: options.timeZone } : {}),
  }).format(date);
}

/** Month and year ("September 2026"), for a month bucket.
 * @kind null
 * @reason Format utility: converts data to display or storage text.
 */
export function formatMonth(value: DateFormatValue, options: HumanDateOptions = {}): string {
  const date = dateFromValue(value);
  if (!date) return "";
  return new Intl.DateTimeFormat(localeCode(options.locale), {
    month: "long",
    year: "numeric",
    ...(options.timeZone ? { timeZone: options.timeZone } : {}),
  }).format(date);
}

/** The calendar day of an instant in a zone, as a sortable key ("2026-09-30"). */
function calendarDay(date: Date, timeZone?: string): string {
  return new Intl.DateTimeFormat("en-CA", {
    year: "numeric", month: "2-digit", day: "2-digit",
    ...(timeZone ? { timeZone } : {}),
  }).format(date);
}

/**
 * The label of a day bucket: "Today", "Yesterday", the weekday within the last
 * week, then the compact date. One owner for every day separator, so two rows
 * on the same calendar day (in the given zone) always share one label.
 * @kind null
 * @reason Format utility: converts data to display or storage text.
 */
export function formatDayLabel(value: DateFormatValue, options: HumanDateOptions = {}): string {
  const date = dateFromValue(value);
  if (!date) return "";
  const now = options.now ?? new Date();
  const locale = localeCode(options.locale);
  const day = calendarDay(date, options.timeZone);
  const elapsedDays = Math.round(
    (Date.parse(calendarDay(now, options.timeZone)) - Date.parse(day)) / 86_400_000,
  );
  if (elapsedDays === 0 || elapsedDays === 1) {
    const label = new Intl.RelativeTimeFormat(locale, { numeric: "auto" }).format(-elapsedDays, "day");
    return label.charAt(0).toLocaleUpperCase(locale) + label.slice(1);
  }
  if (elapsedDays > 1 && elapsedDays < 7) {
    return new Intl.DateTimeFormat(locale, {
      weekday: "long",
      ...(options.timeZone ? { timeZone: options.timeZone } : {}),
    }).format(date);
  }
  return formatDate(date, { ...options, now });
}

/** Full timestamp; explicit zones retain seconds, while local widget labels omit them.
 * @kind null
 * @reason Format utility: converts data to display or storage text.
 */
export function formatDateTime(
  value: DateFormatValue,
  options: HumanDateOptions = {},
): string {
  const date = dateFromValue(value);
  if (!date) return "";
  if (options.density === "list") return formatDate(date, options);
  return new Intl.DateTimeFormat(localeCode(options.locale), {
    dateStyle: "medium",
    // Zoned formatting historically includes seconds; local widget labels do not.
    timeStyle: options.timeZone ? "medium" : "short",
    ...(options.timeZone ? { timeZone: options.timeZone } : {}),
  }).format(date);
}

/** Format a value for a native `type=time` control.
 * @kind null
 * @reason Format utility: converts data to display or storage text.
 */
export function formatTimeInput(value: DateFormatValue): string {
  const date = dateFromValue(value);
  return date ? format(date, TIME_INPUT_FORMAT) : "";
}

/** Format a selected calendar day for storage.
 * @kind null
 * @reason Format utility: converts data to display or storage text.
 */
export function formatDateStorage(value: Date | null): string | null {
  return value ? format(value, DATE_STORAGE_FORMAT) : null;
}

/** Format a selected date-time for storage.
 * @kind null
 * @reason Format utility: converts data to display or storage text.
 */
export function formatDateTimeStorage(value: Date): string {
  return format(value, DATETIME_STORAGE_FORMAT);
}
