import type { QueryClient } from "@tanstack/react-query";

import { recordValue } from "./dialect/wire";

export function authoredQueryMeta(
  modelLabels: readonly string[],
): Record<string, unknown> | undefined {
  return modelLabels.length > 0 ? { angeeModels: [...modelLabels] } : undefined;
}

/**
 * Exact-match authored query metadata against canonical model labels supplied by
 * the caller; this metadata-free layer deliberately performs no alias mapping.
 */
export function authoredQueryReadsAnyModel(
  meta: unknown,
  modelLabels: readonly string[],
): boolean {
  const models = recordValue(meta)?.angeeModels;
  if (!Array.isArray(models)) return false;
  const wanted = new Set(modelLabels);
  return models.some((model) => typeof model === "string" && wanted.has(model));
}

/** Refetch every active authored read registered against one of the moved models. */
export function invalidateAuthoredQueries(
  queryClient: QueryClient,
  modelLabels: readonly string[],
): Promise<void> {
  return queryClient.invalidateQueries({
    predicate: (query) => authoredQueryReadsAnyModel(query.meta, modelLabels),
    type: "all",
    refetchType: "active",
  });
}
