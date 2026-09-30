import { format, formatDistance, isDate, isValid, parseISO, type Locale } from "date-fns";

/** A date-ish widget value: an ISO string, a `Date`, or empty. */
export type DateWidgetValue = string | Date | null;
export type DateFormatValue = DateWidgetValue | number | undefined;

export const DATE_STORAGE_FORMAT = "yyyy-MM-dd";
export const DATETIME_STORAGE_FORMAT = "yyyy-MM-dd'T'HH:mm";
export const TIME_INPUT_FORMAT = "HH:mm";

/** Parse a widget value to a valid `Date`, or null for empty/invalid input. */
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

/** The raw value as a stable title/string (the ISO form for a `Date`). */
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
}

let humanDateLocale = "en";

/** Set the default language for pure date formatters from the app i18n owner. */
export function setHumanDateLocale(language: string): void {
  humanDateLocale = language || "en";
}

function localeCode(locale?: string | Locale): string {
  return typeof locale === "string" ? locale : locale?.code ?? humanDateLocale;
}

/** Compact calendar date, including the year only outside the current year. */
export function formatDate(value: DateFormatValue, options: HumanDateOptions = {}): string {
  const date = dateFromValue(value);
  if (!date) return "";
  const now = options.now ?? new Date();
  const year = options.timeZone
    ? (value: Date) => new Intl.DateTimeFormat("en", { year: "numeric", timeZone: options.timeZone }).format(value)
    : (value: Date) => String(value.getFullYear());
  return new Intl.DateTimeFormat(localeCode(options.locale), {
    month: "short",
    day: "numeric",
    ...(year(date) !== year(now) ? { year: "numeric" } : {}),
    ...(options.timeZone ? { timeZone: options.timeZone } : {}),
  }).format(date);
}

/** Compact endpoints separated by an en dash; callers may add a time to each endpoint. */
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

/** Human elapsed time using date-fns distance for its locales and Intl for app language tags. */
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

/** Localized numeric duration, compact in tables and full in prose. */
export function formatDuration(
  value: number | null | undefined,
  unit: "day" | "week" | "month" | "year",
  options: { locale?: string | Locale; style?: "compact" | "full" } = {},
): string {
  if (value == null || !Number.isFinite(value)) return "";
  const locale = localeCode(options.locale);
  const language = locale.split("-")[0];
  // Intl's English short unit is "wks"; the compact table vocabulary uses "wk".
  if (options.style !== "full" && language === "en") {
    const unitLabel = { day: "d", week: "wk", month: "mo", year: "yr" }[unit];
    return `${new Intl.NumberFormat(locale, { maximumFractionDigits: 2 }).format(value)} ${unitLabel}`;
  }
  return new Intl.NumberFormat(locale, {
    style: "unit",
    unit,
    unitDisplay: options.style === "full" ? "long" : "short",
    maximumFractionDigits: 2,
  }).format(value);
}

/** Full timestamp; explicit zones retain seconds, while local widget labels omit them. */
export function formatDateTime(
  value: DateFormatValue,
  options: { timeZone?: string; locale?: string | Locale } = {},
): string {
  const date = dateFromValue(value);
  if (!date) return "";
  return new Intl.DateTimeFormat(localeCode(options.locale), {
    dateStyle: "medium",
    // Zoned formatting historically includes seconds; local widget labels do not.
    timeStyle: options.timeZone ? "medium" : "short",
    ...(options.timeZone ? { timeZone: options.timeZone } : {}),
  }).format(date);
}

/** Format a value for a native `type=time` control. */
export function formatTimeInput(value: DateFormatValue): string {
  const date = dateFromValue(value);
  return date ? format(date, TIME_INPUT_FORMAT) : "";
}

/** Format a selected calendar day for storage. */
export function formatDateStorage(value: Date | null): string | null {
  return value ? format(value, DATE_STORAGE_FORMAT) : null;
}

/** Format a selected date-time for storage. */
export function formatDateTimeStorage(value: Date): string {
  return format(value, DATETIME_STORAGE_FORMAT);
}
