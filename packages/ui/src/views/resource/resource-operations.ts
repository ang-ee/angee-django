import { useCallback } from "react";
import { useToast } from "../../feedback";
import { useUiT } from "../../i18n";
import {
  refineResourceIdentifier,
  refineResourceName,
  resourceOperationTarget,
  refineInvalidationParams,
  type DataResourceMetadata,
  type DataResourceRootMetadata,
  type ModelMetadata,
} from "@angee/metadata";
import {
  maybeOperationDocument,
  useAngeeDeletePreview,
  useOperationDocuments,
  useReconcileMutationResponse,
  type CustomGraphQLOperationTarget,
  type MutationExecutionOptions,
  type ListBatchTarget,
  type OperationDocumentKind,
  type UseAngeeDeletePreviewResult,
} from "@angee/refine";

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
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function useDeletePreviewOperation(
  resource: DataResourceMetadata | null,
): ResourceOperation {
  return useResourceOperation(resource, "deletePreview", "deletePreviews");
}

export interface UseDeleteWithPreviewResult extends UseAngeeDeletePreviewResult {
  available: boolean;
  /** Resolves false when confirmation has no result or is blocked; rejects transport failures. */
  remove: (id: string, execution?: MutationExecutionOptions & Parameters<UseAngeeDeletePreviewResult["mutate"]>[1]) => Promise<boolean>;
}

/**
 * Own the standard single-record delete-preview confirmation plus Refine cache
 * invalidation tail. Domain callers retain their own confirmation copy and any
 * related-resource invalidations, while the generated operation/document seam
 * stays in one place.
 * @owner DeletePreviewDialog
 */
export function useDeleteWithPreview(
  resource: DataResourceMetadata | null,
): UseDeleteWithPreviewResult {
  const operation = useDeletePreviewOperation(resource);
  const toast = useToast();
  const t = useUiT();
  const preview = useAngeeDeletePreview(operation.target, {
    document: operation.document,
  });
  const reconcileMutationResponse = useReconcileMutationResponse();
  const remove = useCallback(async (id: string, execution?: MutationExecutionOptions & Parameters<UseAngeeDeletePreviewResult["mutate"]>[1]): Promise<boolean> => {
    if (!resource || !operation.target) {
      throw new Error("Delete preview is unavailable for this resource.");
    }
    const { errorNotification, ...reconciliation } = execution ?? {};
    const result = await preview.mutate({ id, confirm: true }, { errorNotification });
    if (!result || result.hasBlockers) {
      toast.danger({ title: t("deletePreview.failedTitle"), description: t(result ? "deletePreview.blockedGeneric" : "deletePreview.emptyPreview") });
      return false;
    }
    await reconcileMutationResponse(undefined, undefined, {
      ...reconciliation,
      records: [{ model: resource.modelLabel, id }],
      invalidates: [refineInvalidationParams({
        resource: refineResourceIdentifier(resource), dataProviderName: resource.schemaName, id,
      })],
    });
    return true;
  }, [operation.target, preview.mutate, reconcileMutationResponse, resource, t, toast]);
  return {
    ...preview,
    available: Boolean(resource && operation.target && operation.document),
    remove,
  };
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

export function groupedListTarget(resource: DataResourceMetadata | null) {
  const list = listBatchTarget(resource);
  if (!list || !resource) return null;
  const { groupedRows: groupedRowsRoot } = resource.roots;
  const { groupBySpec: groupByType, groupedRowPage: groupedRowPageType } = resource.typeNames;
  if (!groupedRowsRoot || !groupByType || !groupedRowPageType) return null;
  return { ...list, groupedRowsRoot, groupByType, groupedRowPageType };
}
