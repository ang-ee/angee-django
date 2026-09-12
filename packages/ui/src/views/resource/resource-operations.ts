import {
  refineResourceIdentifier,
  refineResourceName,
  resourceOperationTarget,
  type DataResourceMetadata,
  type DataResourceRootMetadata,
  type ModelMetadata,
} from "@angee/metadata";
import {
  maybeOperationDocument,
  useInvalidateAuthoredModels,
  useOperationDocuments,
  type CustomGraphQLOperationTarget,
  type ListBatchTarget,
  type OperationDocumentKind,
} from "@angee/refine";
import { useInvalidate } from "@refinedev/core";
import * as React from "react";

export interface ResourceOperation {
  target: CustomGraphQLOperationTarget | null;
  document: unknown;
}

const ABSENT_OPERATION: ResourceOperation = { target: null, document: null };

/** Fail-fast owner for render paths that require model data-resource metadata. */
export function requireDataResource(
  resourceId: string,
  metadata: ModelMetadata | null | undefined,
): DataResourceMetadata {
  const dataResource = metadata?.resource;
  if (!dataResource) {
    throw new Error(`Resource "${resourceId}" has no data resource metadata.`);
  }
  return dataResource;
}

/**
 * Capability probe for one generated resource operation: absent root or
 * absent generated document reads as "capability unavailable" (null target)
 * so an unconditional hook call never fails a render; executing paths gate on
 * the returned target.
 */
function useResourceOperation(
  resource: DataResourceMetadata | null,
  root: keyof DataResourceRootMetadata,
  kind: OperationDocumentKind,
): ResourceOperation {
  const documents = useOperationDocuments();
  if (!resource || !resource.roots[root]) return ABSENT_OPERATION;
  const document = maybeOperationDocument(
    documents,
    resource.schemaName,
    kind,
    resource.modelLabel,
  );
  if (!document) return ABSENT_OPERATION;
  return { target: resourceOperationTarget(resource, root), document };
}

export function useAggregateOperation(
  resource: DataResourceMetadata | null,
): ResourceOperation {
  return useResourceOperation(resource, "aggregate", "aggregates");
}

export function useGroupOperation(
  resource: DataResourceMetadata | null,
): ResourceOperation {
  return useResourceOperation(resource, "groups", "groups");
}

/**
 * Refresh everything that reads one data resource after a write to it.
 *
 * Two reads answer to two different keys, and a caller that writes one by hand
 * reliably forgets the other. `useInvalidate` builds its query key from the
 * resource string verbatim -- it never resolves a name through the registry --
 * so a keyed read only refreshes when handed `refineResourceIdentifier`, the
 * same string `useAngeeListBatch` registers under. Aggregates, facets and
 * grouped reads are custom queries carrying no resource key at all, and answer
 * only to their registered model interest.
 */
export function useInvalidateDataResource(): (
  resource: DataResourceMetadata,
  id?: string,
) => Promise<void> {
  const invalidate = useInvalidate();
  const invalidateAuthoredModels = useInvalidateAuthoredModels();
  return React.useCallback(
    async (resource: DataResourceMetadata, id?: string) => {
      invalidateAuthoredModels([resource.modelLabel]);
      await invalidate({
        resource: refineResourceIdentifier(resource),
        dataProviderName: resource.schemaName,
        ...(id ? { id } : {}),
        invalidates: ["list", "many", "detail"],
      });
    },
    [invalidate, invalidateAuthoredModels],
  );
}

export function useDeletePreviewOperation(
  resource: DataResourceMetadata | null,
): ResourceOperation {
  return useResourceOperation(resource, "deletePreview", "deletePreviews");
}

export function useRevisionOperation(
  resource: DataResourceMetadata | null,
): ResourceOperation {
  return useResourceOperation(resource, "revisions", "revisions");
}

export function useSaveOperation(
  resource: DataResourceMetadata | null,
): ResourceOperation {
  return useResourceOperation(resource, "save", "saves");
}

export function listBatchTarget(
  resource: DataResourceMetadata | null,
): ListBatchTarget | null {
  if (!resource) return null;
  const { list: root, aggregate: aggregateRoot } = resource.roots;
  const { filter: filterType, order: orderType } = resource.typeNames;
  if (!root || !aggregateRoot || !filterType || !orderType) return null;
  return {
    root,
    aggregateRoot,
    filterType,
    orderType,
    dataProviderName: resource.schemaName,
    resourceIdentifier: refineResourceIdentifier(resource),
    resourceName: refineResourceName(resource),
  };
}
