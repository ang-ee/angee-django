import type { Tone } from "@angee/ui";

import type { MessagingT } from "./i18n";

/** The server-derived due states a surface marks, with their tones; other states stay unmarked. */
export const ACTIVITY_STATE_TONES = {
  overdue: "danger",
  today: "warning",
} as const satisfies Readonly<Record<string, Tone>>;

/** One activity's state label: a closed status first, then its server-derived due state. */
export function activityStateLabel(
  activity: { readonly status: string; readonly state: string },
  t: MessagingT,
): string {
  if (activity.status === "DONE") return t("activity.stateDone");
  if (activity.status === "CANCELED") return t("activity.stateCanceled");
  if (activity.state === "overdue") return t("activity.stateOverdue");
  if (activity.state === "today") return t("activity.stateToday");
  return t("activity.statePlanned");
}
