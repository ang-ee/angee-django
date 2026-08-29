import * as React from "react";
import {
  useAuthoredMutation,
  type AuthoredDocument,
  type AuthoredMutate,
  type AuthoredMutationOptions,
  type AuthoredVariables,
  type DocumentData,
} from "@angee/refine";
import {
  useCanonicalResourceModelLabels,
  useResourceInvalidates,
} from "@angee/metadata";

/**
 * `useAuthoredMutation` with resource-backed invalidation wired in.
 *
 * An authored (non-CRUD) mutation's `invalidateModels` only refetches authored
 * *reads* registered with those model labels; the standard refine resource caches
 * (the list/detail of those models) are left untouched. A chrome contribution that
 * writes through an authored verb — a product rating, an inventory adjustment —
 * must also refresh the resource views bound to those models. This owner resolves
 * them through `@angee/metadata`'s `useResourceInvalidates` — the same fold
 * `useRecordActionMutation` composes — and feeds the result as refine
 * `invalidates`, so the contribution inherits resource invalidation without
 * re-deriving it.
 *
 * Each `invalidateModels` entry is canonicalized at this rendered metadata edge;
 * unknown spellings warn in development and are omitted. A non-resource authored
 * read model stays on plain `useAuthoredMutation`. The canonical labels are also
 * forwarded, so authored reads registered on those exact models refetch too.
 */
export function useAuthoredResourceMutation<TDocument extends AuthoredDocument>(
  document: TDocument,
  options: AuthoredMutationOptions<
    DocumentData<TDocument>,
    AuthoredVariables<TDocument>
  > = {},
): [AuthoredMutate<TDocument>, { fetching: boolean; error: Error | null }] {
  const canonicalInvalidateModels = useCanonicalResourceModelLabels(
    options.invalidateModels,
  );
  const resourceInvalidates = useResourceInvalidates(canonicalInvalidateModels);
  const invalidates = React.useMemo(
    () => [...(options.invalidates ?? []), ...resourceInvalidates],
    [options.invalidates, resourceInvalidates],
  );
  return useAuthoredMutation(document, {
    ...options,
    ...(options.invalidateModels !== undefined
      ? { invalidateModels: canonicalInvalidateModels }
      : {}),
    invalidates,
  });
}
