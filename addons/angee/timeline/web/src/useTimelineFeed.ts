import { useAuth } from "@angee/app";
import { useAuthoredKeysetFeed, type AuthoredVariables } from "@angee/refine";

import { TimelineDocument } from "./documents.console";

type Variables = Omit<AuthoredVariables<typeof TimelineDocument>, "before">;

/** The timeline supplies snapshot cursors; native Query owns loaded history and refresh. */
export function useTimelineFeed(variables: Variables) {
  return useAuthoredKeysetFeed({
    actor: useAuth().user?.id,
    enabled: Boolean(variables.model && variables.id),
    models: [variables.model],
    records: [{ model: variables.model, id: String(variables.id) }],
    pageSize: variables.limit ?? 25,
    window: {
      document: TimelineDocument,
      variables: (before, _through, limit) => ({ kinds: null, needsMe: false, ...variables, before, limit }),
      select: ({ timeline: page }) => ({
        rows: page.entries.map((entry) => ({ ...entry, id: entry.key })),
        count: page.entries.length,
        metadata: { kinds: page.kinds, needsMe: page.needs_me },
        older_cursor: page.next_before,
        has_older: page.has_more,
        has_more_in_window: false,
        has_older_than_through: false,
      }),
    },
  }).query;
}
