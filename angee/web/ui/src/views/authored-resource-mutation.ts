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
  refineInvalidationParams,
  resourceInvalidationTargets,
  useSchemaFieldMetadata,
} from "@angee/metadata";

/**
 * `useAuthoredMutation` with resource-backed invalidation wired in.
 *
 * An authored (non-CRUD) mutation's `invalidateModels` only refetches authored
 * *reads* registered with those model labels; the standard refine resource caches
 * (the list/detail of those models) are left untouched. A chrome contribution that
 * writes through an authored verb — a product rating, an inventory adjustment —
 * must also refresh the resource views bound to those models. This owner maps each
 * model label through the metadata edge (`resourceInvalidationTargets` →
 * `refineInvalidationParams`, the same path `useRecordActionMutation` uses) and
 * feeds them as refine `invalidates`, so the contribution inherits resource
 * invalidation without re-deriving it.
 *
 * Each `invalidateModels` entry must be a model exposed in resource metadata (the
 * mapping throws otherwise); a non-resource authored read model stays on plain
 * `useAuthoredMutation`. `invalidateModels` is still forwarded, so any authored
 * read registered on those models refetches too.
 */
export function useAuthoredResourceMutation<TDocument extends AuthoredDocument>(
  document: TDocument,
  options: AuthoredMutationOptions<
    DocumentData<TDocument>,
    AuthoredVariables<TDocument>
  > = {},
): [AuthoredMutate<TDocument>, { fetching: boolean; error: Error | null }] {
  const schemaMetadata = useSchemaFieldMetadata();
  const invalidates = React.useMemo(
    () => [
      ...(options.invalidates ?? []),
      ...resourceInvalidationTargets(
        schemaMetadata,
        options.invalidateModels ?? [],
      ).map(refineInvalidationParams),
    ],
    [schemaMetadata, options.invalidates, options.invalidateModels],
  );
  return useAuthoredMutation(document, { ...options, invalidates });
}
