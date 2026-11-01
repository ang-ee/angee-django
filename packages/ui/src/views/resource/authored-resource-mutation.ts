import * as React from "react";
import {
  useAuthoredMutation,
  type AuthoredDocument,
  type AuthoredMutationOptions,
  type AuthoredVariables,
  type DocumentData,
} from "@angee/refine";
import {
  useResourceInvalidates,
  useResourceMutationModels,
} from "@angee/metadata";

/**
 * `useAuthoredMutation` with resource-backed invalidation wired in.
 *
 * An authored (non-CRUD) mutation's `invalidateModels` only refetches authored
 * *reads* registered with those model labels; the standard refine resource caches
 * (the list/detail of those models) are left untouched. A chrome contribution that
 * writes through an authored verb — a product rating, an inventory adjustment —
 * must also refresh the resource views bound to those models. This owner resolves
 * them through `@angee/metadata`'s resource invalidation mapping — the same
 * owner `useRecordActionMutation` composes — and feeds the result as refine
 * `invalidates`, so the contribution inherits resource invalidation without
 * re-deriving it.
 *
 * Resource aliases are canonicalized at this rendered metadata edge. Authored
 * model effects without a native CRUD resource retain their declared canonical
 * label and stay on Refine's authored/live path. Only resource-backed effects
 * map to native resource caches; neither set replaces the other.
 * Declared `records` narrow detail effects to their actual ids; collections still
 * reconcile membership and totals. `acceptResponse` adopts the caller's typed
 * projection before the shared Refine owner schedules those effects.
 * @kind null
 * @reason Codec and transport infrastructure: adds resource-cache invalidation to authored GraphQL mutations.
 */
export function useAuthoredResourceMutation<TDocument extends AuthoredDocument>(
  document: TDocument,
  options: AuthoredMutationOptions<
    DocumentData<TDocument>,
    AuthoredVariables<TDocument>
  > = {},
): ReturnType<typeof useAuthoredMutation<TDocument>> {
  const { models, resourceModels } = useResourceMutationModels(options.invalidateModels);
  const resourceInvalidates = useResourceInvalidates(resourceModels, options.records);
  const invalidates = React.useMemo(() => {
    const explicit = options.invalidates;
    if (typeof explicit !== "function" && typeof resourceInvalidates !== "function") {
      return [...(explicit ?? []), ...resourceInvalidates];
    }
    return (data: DocumentData<TDocument> | undefined, variables: AuthoredVariables<TDocument>) => [
      ...(typeof explicit === "function" ? explicit(data, variables) : explicit ?? []),
      ...(typeof resourceInvalidates === "function" ? resourceInvalidates(data, variables) : resourceInvalidates),
    ];
  }, [options.invalidates, resourceInvalidates]);
  return useAuthoredMutation(document, {
    ...options,
    ...(options.invalidateModels !== undefined
      ? { invalidateModels: models }
      : {}),
    invalidates,
  });
}
