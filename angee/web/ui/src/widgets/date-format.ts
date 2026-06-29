import { format, isValid, parseISO } from "date-fns";

/** A date-ish widget value: an ISO string, a `Date`, or empty. */
export type DateWidgetValue = string | Date | null;
export type DateFormatValue = DateWidgetValue | number | undefined;

export const DATE_DISPLAY_FORMAT = "MMM d, yyyy";
export const DATETIME_DISPLAY_FORMAT = "MMM d, yyyy, p";
export const DATE_STORAGE_FORMAT = "yyyy-MM-dd";
export const DATETIME_STORAGE_FORMAT = "yyyy-MM-dd'T'HH:mm";
export const TIME_INPUT_FORMAT = "HH:mm";

/** Parse a widget value to a valid `Date`, or null for empty/invalid input. */
export function dateFromValue(value: DateFormatValue): Date | null {
  if (value instanceof Date) return isValid(value) ? value : null;
  if (typeof value === "number") {
    const date = new Date(value);
    return isValid(date) ? date : null;
  }
  if (!value) return null;
  const parsed = parseISO(value);
  return isValid(parsed) ? parsed : null;
}

/** Parse any row/display value to a valid `Date`, or null for non-date values. */
export function dateFromUnknown(value: unknown): Date | null {
  if (value instanceof Date || typeof value === "number" || typeof value === "string") {
    return dateFromValue(value);
  }
  return null;
}

/** The raw value as a stable title/string (the ISO form for a `Date`). */
export function valueLabel(value: DateFormatValue): string {
  if (value instanceof Date) return isValid(value) ? value.toISOString() : "";
  if (typeof value === "number") return String(value);
  return value ?? "";
}

/** Format a date-only value for display; empty/invalid values render empty. */
export function formatDate(value: DateFormatValue): string {
  const date = dateFromValue(value);
  return date ? format(date, DATE_DISPLAY_FORMAT) : "";
}

/** Format a date-time value for display; empty/invalid values render empty. */
export function formatDateTime(value: DateFormatValue): string {
  const date = dateFromValue(value);
  return date ? format(date, DATETIME_DISPLAY_FORMAT) : "";
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
