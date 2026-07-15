import { useMemo } from "react";
import type { UseInvalidateProp } from "@refinedev/core";

import {
  modelMetadataForLabel,
  useSchemaFieldMetadata,
  type SchemaFieldMetadata,
} from "./metadata";
import { refineResourceName } from "./resources";

export interface ResourceInvalidationTarget {
  resource: string;
  dataProviderName: string;
}

export function resourceInvalidationTargets(
  metadata: SchemaFieldMetadata,
  modelLabels: readonly string[],
): readonly ResourceInvalidationTarget[] {
  if (modelLabels.length === 0 || !metadata.resources?.length) return [];
  return modelLabels.map((modelLabel) => {
    const model = modelMetadataForLabel(metadata, modelLabel);
    const resource = model?.resource;
    if (!resource) {
      throw new Error(
        `Action invalidation target "${modelLabel}" is not exposed in resource metadata.`,
      );
    }
    return {
      resource: refineResourceName(resource),
      dataProviderName: resource.schemaName,
    };
  });
}

export function refineInvalidationParams(
  target: ResourceInvalidationTarget,
): UseInvalidateProp {
  return {
    resource: target.resource,
    dataProviderName: target.dataProviderName,
    invalidates: ["list", "many", "detail"],
  };
}

/**
 * The refine `invalidates` a verb's mutated Angee model labels map to.
 *
 * The one fold of model labels through this module's pair
 * ({@link resourceInvalidationTargets} → {@link refineInvalidationParams}): a
 * hook that moves a named model's resource caches composes this instead of
 * repeating the pair against the ambient metadata. Stabilized by label contents,
 * so a caller passing a fresh array literal each render does not churn the
 * mutation options it feeds.
 */
export function useResourceInvalidates(
  modelLabels: readonly string[] | undefined,
): readonly UseInvalidateProp[] {
  const metadata = useSchemaFieldMetadata();
  const key = JSON.stringify(modelLabels ?? []);
  return useMemo(
    () =>
      resourceInvalidationTargets(metadata, modelLabels ?? []).map(
        refineInvalidationParams,
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key, metadata],
  );
}
