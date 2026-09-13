import { useCallback } from "react";
import { useInvalidate } from "@refinedev/core";
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
  useAngeeDeletePreview,
  useOperationDocuments,
  type CustomGraphQLOperationTarget,
  type ListBatchTarget,
  type OperationDocumentKind,
  type UseAngeeDeletePreviewResult,
} from "@angee/refine";
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

export interface UseDeleteWithPreviewResult extends UseAngeeDeletePreviewResult {
  available: boolean;
  remove: (id: string) => Promise<void>;
}

/**
 * Own the standard single-record delete-preview confirmation plus Refine cache
 * invalidation tail. Domain callers retain their own confirmation copy and any
 * related-resource invalidations, while the generated operation/document seam
 * stays in one place.
 */
export function useDeleteWithPreview(
  resource: DataResourceMetadata | null,
): UseDeleteWithPreviewResult {
  const operation = useDeletePreviewOperation(resource);
  const preview = useAngeeDeletePreview(operation.target, {
    document: operation.document,
  });
  const invalidate = useInvalidate();
  const remove = useCallback(async (id: string): Promise<void> => {
    if (!resource || !operation.target) {
      throw new Error("Delete preview is unavailable for this resource.");
    }
    await preview.mutate({ id, confirm: true });
    await invalidate({
      resource: refineResourceName(resource),
      dataProviderName: resource.schemaName,
      id,
      invalidates: ["list", "many", "detail"],
    });
  }, [invalidate, operation.target, preview.mutate, resource]);
  return {
    ...preview,
    available: Boolean(resource && operation.target && operation.document),
    remove,
  };
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
