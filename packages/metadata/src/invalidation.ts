import { useMemo } from "react";
import type { UseInvalidateProp } from "@refinedev/core";

import {
  canonicalModelLabel,
  canonicalModelLabelOrNull,
} from "./canonical-model-label";
import {
  modelMetadataForLabel,
  type SchemaFieldMetadata,
} from "./artifact";
import { useSchemaFieldMetadata } from "./context";
import { refineResourceIdentifier } from "./resources";

export interface ResourceInvalidationTarget {
  resource: string;
  dataProviderName: string;
}

export function resourceInvalidationTargets(
  metadata: SchemaFieldMetadata,
  modelLabels: readonly string[],
): readonly ResourceInvalidationTarget[] {
  if (modelLabels.length === 0) return [];
  return modelLabels.map((spelling) => {
    const modelLabel = canonicalModelLabel(metadata.resources ?? [], spelling);
    const model = modelMetadataForLabel(metadata, modelLabel);
    const resource = model?.resource;
    if (!resource) {
      throw new Error(
        `Action invalidation target "${spelling}" is not exposed in resource metadata.`,
      );
    }
    return {
      resource: refineResourceIdentifier(resource),
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
    // Refine's `detail` invalidation requires an id. Authored verbs name every
    // model they mutate, but may update a related model whose id is not the
    // action subject. Invalidate the declared resource prefix so its active
    // list, many, and real-id detail queries all refresh.
    invalidates: ["resourceAll"],
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
  const canonicalModelLabels = useCanonicalResourceModelLabels(modelLabels);
  const key = JSON.stringify(canonicalModelLabels);
  return useMemo(
    () =>
      resourceInvalidationTargets(metadata, canonicalModelLabels).map(
        refineInvalidationParams,
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key, metadata],
  );
}

/**
 * Resolve authored-operation model labels at the rendered metadata edge.
 * Refine's authored hooks intentionally stay metadata-free and require exact
 * canonical labels; render-time unknowns warn in development and are omitted.
 */
export function useCanonicalResourceModelLabels(
  modelLabels: readonly string[] | undefined,
): readonly string[] {
  const metadata = useSchemaFieldMetadata();
  const key = JSON.stringify(modelLabels ?? []);
  return useMemo(() => {
    const canonical = (modelLabels ?? []).flatMap((spelling) => {
      const label = canonicalModelLabelOrNull(
        metadata.resources ?? [],
        spelling,
        "authored-operation model label",
      );
      return label ? [label] : [];
    });
    return [...new Set(canonical)];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, metadata]);
}
