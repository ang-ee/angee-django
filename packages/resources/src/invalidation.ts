import type { UseInvalidateProp } from "@refinedev/core";

import {
  modelMetadataForLabel,
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
