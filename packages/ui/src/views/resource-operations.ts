import {
  refineResourceIdentifier,
  refineResourceName,
  resourceOperationTarget,
  type DataResourceMetadata,
  type DataResourceRootMetadata,
} from "@angee/metadata";
import {
  maybeOperationDocument,
  useOperationDocuments,
  type CustomGraphQLOperationTarget,
  type ListBatchTarget,
  type OperationDocumentKind,
} from "@angee/refine";

export interface ResourceOperation {
  target: CustomGraphQLOperationTarget | null;
  document: unknown;
}

const ABSENT_OPERATION: ResourceOperation = { target: null, document: null };

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
  return {
    dataProviderName: resource.schemaName,
    resourceIdentifier: refineResourceIdentifier(resource),
    resourceName: refineResourceName(resource),
  };
}
