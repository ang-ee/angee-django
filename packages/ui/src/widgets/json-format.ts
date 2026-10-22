/**
 * The one JSON-to-text formatter: pretty (two-space) for a body, `compact` for a
 * cell. `undefined` formats empty; a value JSON cannot serialize falls back to
 * its string form.
 * @kind null
 * @reason Format utility: converts data to display or storage text.
 */
export function formatJson(value: unknown, options: { compact?: boolean } = {}): string {
  if (value === undefined) return "";
  try {
    return JSON.stringify(value, null, options.compact ? undefined : 2) ?? "";
  } catch {
    return String(value);
  }
}
