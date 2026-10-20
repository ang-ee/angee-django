import { createNamespaceT } from "@angee/ui";

/** The timeline addon's own copy: what a kind without a presentation, and a folded count, are called. */
export const enTimelineMessages: Record<string, string> = {
  // A ledger kind no addon presents: its lines still read as activity, never as the kind's id.
  "kind.unknown": "Activity",
  // What folds under a line: the child kind's label and how many, in the pane's "label · count" idiom.
  "folded": "{label} · {count}",
};

export const useTimelineT = createNamespaceT("timeline", enTimelineMessages);
export type TimelineT = ReturnType<typeof useTimelineT>;
