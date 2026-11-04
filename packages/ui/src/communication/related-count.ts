import { useAuthoredQuery, type AuthoredDocument, type AuthoredQueryOptions, type AuthoredVariables, type DocumentData } from "@angee/refine";

import type { ChatterViewContext } from "../runtime";

/** One count-only authored document and its row-equivalent live interests.
 * @owner RelatedPane
 */
export function relatedCount<TDocument extends AuthoredDocument>(
  document: TDocument,
  scope: (context: ChatterViewContext) => { variables: AuthoredVariables<TDocument>; options: AuthoredQueryOptions },
  select: (data: DocumentData<TDocument>) => number | undefined,
): (context: ChatterViewContext) => number | undefined {
  return function useRelatedCount(context) {
    const { variables, options } = scope(context);
    const { data } = useAuthoredQuery(document, variables, options);
    return data === undefined ? undefined : select(data);
  };
}
