/**
 * The one owner of human dates and times. The rule: every list, row, facts line
 * and day label is absolute (`formatDate`, `formatDateTime`, `formatTime`,
 * `formatDayLabel`, `formatMonth`, `formatDateParts`); relative time (`formatRelativeTime`,
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
 * A picked wall time with the browser's UTC offset ("2026-11-20T10:00:00+09:00"):
 * an instant, which a server in any zone reads as the moment the viewer meant.
 * @kind null
 * @reason Format utility: converts data to display or storage text.
 */
export const DATETIME_STORAGE_FORMAT = "yyyy-MM-dd'T'HH:mm:ssxxx";
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
  /** Calendar time zone for a date label, including its current-year decision; one this browser does not know
   * reads in the viewer's zone. */
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

/** Whether this browser's `Intl` knows each zone asked for, decided once per zone. */
const knownZones = new Map<string, boolean>();

/**
 * The `timeZone` option every formatter here passes to `Intl`. An IANA name this browser's zone data lacks
 * (one newer than its `Intl`, say) would throw a `RangeError` from the formatter, failing whatever renders it,
 * so it reads in the viewer's zone instead and is reported once.
 */
function zoneOption(timeZone: string | undefined): { timeZone?: string } {
  if (!timeZone) return {};
  if (!knownZones.has(timeZone)) {
    try {
      new Intl.DateTimeFormat("en", { timeZone });
      knownZones.set(timeZone, true);
    } catch (error) {
      if (!(error instanceof RangeError)) throw error;
      knownZones.set(timeZone, false);
      console.warn(`[angee] Unknown time zone "${timeZone}": dates read in the viewer's zone instead.`);
    }
  }
  return knownZones.get(timeZone) ? { timeZone } : {};
}

/** Compact calendar date, including the year only outside the current year.
 * @kind null
 * @reason Format utility: converts data to display or storage text.
 */
export function formatDate(value: DateFormatValue, options: HumanDateOptions = {}): string {
  const date = dateFromValue(value);
  if (!date) return "";
  const now = options.now ?? new Date();
  const zone = zoneOption(options.timeZone);
  const year = zone.timeZone
    ? (value: Date) => new Intl.DateTimeFormat("en", { year: "numeric", ...zone }).format(value)
    : (value: Date) => String(value.getFullYear());
  return new Intl.DateTimeFormat(localeCode(options.locale), {
    ...(options.weekday ? { weekday: "long" } : {}),
    month: "short",
    day: "numeric",
    ...(options.density === "full" || year(date) !== year(now) ? { year: "numeric" } : {}),
    ...zone,
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

/** A GraphQL `Time` wire value ("13:45", "13:45:30.5"): a time of day with no date and no zone. */
const TIME_OF_DAY = /^([01]\d|2[0-3]):([0-5]\d)(?::([0-5]\d)(?:\.\d+)?)?$/;

/** A time of day at a fixed UTC anchor, so formatting in UTC reads it back as written. */
function timeOfDayFromValue(value: DateFormatValue): Date | null {
  const match = typeof value === "string" ? TIME_OF_DAY.exec(value) : null;
  return match ? new Date(Date.UTC(1970, 0, 1, Number(match[1]), Number(match[2]), Number(match[3] ?? 0))) : null;
}

/** Clock time without seconds ("1:45 PM", "13:45"), for a row whose day a label already names.
 * A GraphQL `Time` value ("13:45:00") is a time of day: it carries no zone and reads as written.
 * @kind null
 * @reason Format utility: converts data to display or storage text.
 */
export function formatTime(value: DateFormatValue, options: HumanDateOptions = {}): string {
  const timeOfDay = timeOfDayFromValue(value);
  const date = timeOfDay ?? dateFromValue(value);
  if (!date) return "";
  return new Intl.DateTimeFormat(localeCode(options.locale), {
    timeStyle: "short",
    ...(timeOfDay ? { timeZone: "UTC" } : zoneOption(options.timeZone)),
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
    ...zoneOption(options.timeZone),
  }).format(date);
}

/** A date's month and day, each localized on its own. */
export interface DateParts {
  month: string;
  day: string;
}

/**
 * The short month and the day of the month, each from its own formatter ("Sep" and
 * "28", "9月" and "28日", "syys" and "28"), for a layout that sets them apart, such as
 * a date tile. A combined month-and-day pattern would pick the month's in-context
 * form, which is numeric in Japanese, Chinese or Finnish.
 * @kind null
 * @reason Format utility: converts data to display or storage text.
 */
export function formatDateParts(value: DateFormatValue, options: HumanDateOptions = {}): DateParts | null {
  const date = dateFromValue(value);
  if (!date) return null;
  const locale = localeCode(options.locale);
  const zone = zoneOption(options.timeZone);
  return {
    month: new Intl.DateTimeFormat(locale, { month: "short", ...zone }).format(date),
    day: new Intl.DateTimeFormat(locale, { day: "numeric", ...zone }).format(date),
  };
}

/** The calendar day of an instant in a zone (the viewer's without one, or for one this browser does not know), as
 * `yyyy-MM-dd` ("2026-09-30"): a sortable
 * key, a `<time datetime>` value, or a stored all-day bound's day in the zone that owns its dates, which every
 * browser then shows as that day.
 * @kind null
 * @reason Format utility: converts data to display or storage text.
 */
export function calendarDay(date: Date, timeZone?: string): string {
  // Built from the parts, never from a locale's own date pattern, which locale data may change.
  const parts = new Intl.DateTimeFormat("en-US", {
    calendar: "gregory", numberingSystem: "latn", year: "numeric", month: "2-digit", day: "2-digit",
    ...zoneOption(timeZone),
  }).formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((entry) => entry.type === type)?.value ?? "";
  return `${part("year").padStart(4, "0")}-${part("month")}-${part("day")}`;
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
      ...zoneOption(options.timeZone),
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
  const zone = zoneOption(options.timeZone);
  return new Intl.DateTimeFormat(localeCode(options.locale), {
    dateStyle: "medium",
    // Zoned formatting historically includes seconds; local widget labels do not.
    timeStyle: zone.timeZone ? "medium" : "short",
    ...zone,
  }).format(date);
}

/** Format a value for a native `type=time` control, whose `HH:mm` value is also a GraphQL `Time` input.
 * A `Time` value keeps its own hours and minutes; any other value is read in local time.
 * @kind null
 * @reason Format utility: converts data to display or storage text.
 */
export function formatTimeInput(value: DateFormatValue): string {
  const timeOfDay = timeOfDayFromValue(value);
  if (timeOfDay) return timeOfDay.toISOString().slice(11, 16);
  const date = dateFromValue(value);
  return date ? format(date, TIME_INPUT_FORMAT) : "";
}

/** Format a selected local calendar day for storage ({@link DATE_STORAGE_FORMAT}); the day an instant falls on in
 * another zone is {@link calendarDay}'s.
 * @kind null
 * @reason Format utility: converts data to display or storage text.
 */
export function formatDateStorage(value: Date | null): string | null {
  return value ? format(value, DATE_STORAGE_FORMAT) : null;
}

/** Format a selected date-time for storage: its local wall time with the browser's offset
 * ({@link DATETIME_STORAGE_FORMAT}), so every writer of a picked moment sends the instant meant, never a
 * naive time the server would read in its own zone.
 * @kind null
 * @reason Format utility: converts data to display or storage text.
 */
export function formatDateTimeStorage(value: Date): string {
  return format(value, DATETIME_STORAGE_FORMAT);
}
