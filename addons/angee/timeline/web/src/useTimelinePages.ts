import { useAuthoredQueryBatch, type AuthoredVariables } from "@angee/refine";

import { TimelineDocument } from "./documents.console";

type Variables = Omit<AuthoredVariables<typeof TimelineDocument>, "before">;
const FIRST_PAGE = [undefined];

/** Header and rail share a read, invalidated through the record's own change feed. */
export function useTimelinePages(variables: Variables, cursors: readonly (string | undefined)[] = FIRST_PAGE) {
  const results = useAuthoredQueryBatch(cursors.map((before, index) => ({
    key: `page-${index}`,
    document: TimelineDocument,
    variables: { kinds: null, needsMe: false, limit: 25, ...variables, before },
    models: [variables.model],
    records: [{ model: variables.model, id: String(variables.id) }],
  })), { enabled: Boolean(variables.model && variables.id) });
  return cursors.map((_, index) => results.get(`page-${index}`));
}
