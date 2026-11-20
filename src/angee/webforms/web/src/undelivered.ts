import { formatDate, optionToken, type ResourceViewFilter } from "@angee/ui";
import type { UiTranslate } from "@angee/ui/i18n";

/**
 * What became of a response its form's target has not taken. This mirrors the
 * server's rule and is not a second one: `Form.undelivered_count` and
 * `undelivered_expires_at` count the responses whose `delivery` is pending or
 * failed (`FormQuerySet.undelivered_expressions` in the backend). The filter
 * and the record's verbs here read the same pair so the list a form links to
 * holds exactly the responses that form counted.
 */
export const UNDELIVERED: readonly string[] = ["pending", "failed"];

/** Whether a response still waits for its form's target: it can be delivered again or given up on. */
export function isUndelivered(response: Record<string, unknown>): boolean {
  return UNDELIVERED.includes(optionToken(response.delivery));
}

/** The responses still waiting for delivery: of every form, or of the one named. */
export function undeliveredFilter(form?: string): ResourceViewFilter {
  return { delivery: { inList: [...UNDELIVERED] }, ...(form ? { form: { exact: form } } : {}) };
}

/**
 * What a form says of the responses it has not delivered: how many there are and
 * when the first of them is removed. Nothing while none waits, and the count
 * alone while the form keeps its responses.
 */
export function undeliveredSummary(form: Record<string, unknown>, t: UiTranslate): string | null {
  const count = typeof form.undelivered_count === "number" ? form.undelivered_count : 0;
  if (count < 1) return null;
  const date = typeof form.undelivered_expires_at === "string" ? formatDate(form.undelivered_expires_at) : "";
  return date ? t("form.undeliveredExpiring", { count, date }) : t("form.undeliveredCount", { count });
}
