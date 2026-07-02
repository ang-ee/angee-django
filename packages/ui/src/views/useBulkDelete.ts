import * as React from "react";
import {
  useCan,
  useInvalidate,
} from "@refinedev/core";
import {
  useAngeeDeletePreview,
  type DeletePreview,
  type DeletePreviewGroup,
  type DeletePreviewNode,
} from "@angee/refine";
import {
  refineResourceName,
} from "@angee/metadata";
import {
  useModelMetadata,
} from "@angee/metadata";
import {
  useModelRootFields,
} from "@angee/metadata";

import { errorMessage, useToast } from "../feedback";
import { useUiT } from "../i18n";
import { useDeletePreviewOperation } from "./resource-operations";

const BULK_DELETE_PREVIEW_LIMIT = 25;

interface PreviewEntry {
  id: string;
  preview: DeletePreview;
}

interface BulkPreviewState {
  preview: DeletePreview;
  selectedIds: readonly string[];
  blockedIds: readonly string[];
  overflowCount: number;
}

export interface UseBulkDeleteResult {
  previewState: DeletePreview | null;
  previewRecordCount: number;
  previewOverflowCount: number;
  previewBlockedRecordCount: number;
  isPreviewOpen: boolean;
  isPending: boolean;
  canDelete: boolean;
  deleteInitiate: () => void;
  onConfirm: () => void;
  onCancel: () => void;
}

export function useBulkDelete(
  resource: string,
  selectedIds: ReadonlySet<string>,
  clearSelectedIds: () => void,
): UseBulkDeleteResult {
  const t = useUiT();
  const toast = useToast();
  const rootFields = useModelRootFields(resource);
  const metadata = useModelMetadata(resource);
  const dataResource = metadata?.resource ?? null;
  const deletePreviewOperation = useDeletePreviewOperation(dataResource);
  const refineResource = dataResource ? refineResourceName(dataResource) : undefined;
  const deleteAccess = useCan({
    resource: refineResource,
    action: "delete",
    queryOptions: { enabled: Boolean(refineResource) },
  });
  const canDelete =
    (rootFields === null || Boolean(rootFields.delete))
    && (deleteAccess.data?.can ?? true);
  const deletePreview = useAngeeDeletePreview(deletePreviewOperation.target, {
    document: deletePreviewOperation.document,
  });
  const invalidate = useInvalidate();
  const mutate = React.useCallback(
    async ({ id, confirm }: { id: string; confirm?: boolean }) => {
      if (!canDelete) {
        throw new Error(`Delete mutation for "${resource}" is disabled.`);
      }
      if (!dataResource) {
        throw new Error(`Resource metadata for "${resource}" is not available.`);
      }
      const preview = await deletePreview.mutate({ id, confirm });
      if (confirm === true) {
        await invalidate({
          resource: refineResourceName(dataResource),
          dataProviderName: dataResource.schemaName,
          id,
          invalidates: ["list", "many", "detail"],
        });
      }
      return preview;
    },
    [
      canDelete,
      dataResource,
      deletePreview.mutate,
      invalidate,
      resource,
    ],
  );
  const selectedIdList = React.useMemo(
    () => [...selectedIds],
    [selectedIds],
  );
  const [previewState, setPreviewState] =
    React.useState<BulkPreviewState | null>(null);
  const [previewPending, setPreviewPending] = React.useState(false);
  const [deletePending, setDeletePending] = React.useState(false);
  const requestIdRef = React.useRef(0);

  React.useEffect(() => {
    return () => {
      requestIdRef.current += 1;
    };
  }, []);

  const deleteInitiate = React.useCallback(() => {
    if (!canDelete || selectedIdList.length === 0) return;
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    const previewedIds = selectedIdList.slice(0, BULK_DELETE_PREVIEW_LIMIT);
    const overflowCount = Math.max(
      0,
      selectedIdList.length - previewedIds.length,
    );
    setPreviewPending(true);
    void Promise.all(
      previewedIds.map(async (id): Promise<PreviewEntry | null> => {
        const preview = await mutate({ id, confirm: false });
        return preview ? { id, preview } : null;
      }),
    )
      .then((entries) => {
        if (requestIdRef.current !== requestId) return;
        const previews = entries.filter(
          (entry): entry is PreviewEntry => entry !== null,
        );
        if (previews.length === 0) {
          toast.danger({
            title: t("deletePreview.failedTitle"),
            description: t("deletePreview.emptyPreview"),
          });
          return;
        }
        const blockedIds = previews
          .filter((entry) => entry.preview.hasBlockers)
          .map((entry) => entry.id);
        setPreviewState({
          preview: aggregatePreviews(previews, overflowCount, t),
          selectedIds: selectedIdList,
          blockedIds,
          overflowCount,
        });
      })
      .catch((error: unknown) => {
        toast.danger({
          title: t("deletePreview.failedTitle"),
          description: errorMessage(
            error,
            t("deletePreview.loadError"),
          ),
        });
      })
      .finally(() => {
        if (requestIdRef.current === requestId) setPreviewPending(false);
      });
  }, [canDelete, mutate, selectedIdList, toast, t]);

  const onCancel = React.useCallback(() => {
    if (deletePending) return;
    requestIdRef.current += 1;
    setPreviewState(null);
    setPreviewPending(false);
  }, [deletePending]);

  const onConfirm = React.useCallback(() => {
    const state = previewState;
    if (!canDelete || !state || deletePending) return;
    const blocked = new Set(state.blockedIds);
    const idsToDelete = state.selectedIds.filter((id) => !blocked.has(id));
    if (idsToDelete.length === 0) return;
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setDeletePending(true);
    void Promise.all(
      idsToDelete.map(async (id) => {
        try {
          const preview = await mutate({ id, confirm: true });
          return { id, preview, error: null };
        } catch (error: unknown) {
          return { id, preview: null, error };
        }
      }),
    )
      .then((results) => {
        if (requestIdRef.current !== requestId) return;
        const blockedByDelete = results.filter(
          (result) => result.preview?.hasBlockers,
        ).length;
        const failed = results.filter((result) => result.error).length;
        const deleted = results.length - blockedByDelete - failed;
        clearSelectedIds();
        setPreviewState(null);
        toastDeleteOutcome({
          toast,
          deleted,
          blocked: state.blockedIds.length + blockedByDelete,
          failed,
          overflowCount: state.overflowCount,
          t,
        });
      })
      .finally(() => {
        if (requestIdRef.current === requestId) setDeletePending(false);
      });
  }, [canDelete, clearSelectedIds, deletePending, mutate, previewState, toast, t]);

  return {
    previewState: previewState?.preview ?? null,
    previewRecordCount: previewState?.selectedIds.length ?? 0,
    previewOverflowCount: previewState?.overflowCount ?? 0,
    previewBlockedRecordCount: previewState?.blockedIds.length ?? 0,
    isPreviewOpen: previewState !== null,
    isPending: previewPending || deletePending,
    canDelete,
    deleteInitiate,
    onConfirm,
    onCancel,
  };
}

function aggregatePreviews(
  entries: readonly PreviewEntry[],
  overflowCount: number,
  t: ReturnType<typeof useUiT>,
): DeletePreview {
  const roots = entries.map((entry) => entry.preview.root);
  return {
    totalDeletedCount: entries.reduce(
      (total, entry) => total + entry.preview.totalDeletedCount,
      0,
    ),
    deleted: aggregateGroups(entries.flatMap((entry) => entry.preview.deleted)),
    updated: aggregateGroups(entries.flatMap((entry) => entry.preview.updated)),
    blocked: aggregateGroups(entries.flatMap((entry) => entry.preview.blocked)),
    hasBlockers: entries.some((entry) => entry.preview.hasBlockers),
    root: aggregateRoot(roots, overflowCount, t),
  };
}

function aggregateRoot(
  roots: readonly DeletePreviewNode[],
  overflowCount: number,
  t: ReturnType<typeof useUiT>,
): DeletePreviewNode {
  return {
    label: t("deletePreview.selection"),
    objectLabel: t("deletePreview.recordCount", { count: roots.length }),
    objectId: null,
    children: overflowCount > 0
      ? [...roots, moreNode(overflowCount, t)]
      : roots,
  };
}

function moreNode(count: number, t: ReturnType<typeof useUiT>): DeletePreviewNode {
  return {
    label: "",
    objectLabel: t("deletePreview.more", { count }),
    objectId: null,
    children: [],
  };
}

function aggregateGroups(
  groups: readonly DeletePreviewGroup[],
): DeletePreviewGroup[] {
  const counts = new Map<string, number>();
  for (const group of groups) {
    counts.set(group.label, (counts.get(group.label) ?? 0) + group.count);
  }
  return [...counts.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([label, count]) => ({ label, count }));
}

function toastDeleteOutcome({
  toast,
  deleted,
  blocked,
  failed,
  overflowCount,
  t,
}: {
  toast: ReturnType<typeof useToast>;
  deleted: number;
  blocked: number;
  failed: number;
  overflowCount: number;
  t: ReturnType<typeof useUiT>;
}) {
  const details = [
    blocked > 0 ? t("deletePreview.blockedDetail", { count: blocked }) : "",
    failed > 0 ? t("deletePreview.failedDetail", { count: failed }) : "",
    overflowCount > 0
      ? t("deletePreview.notPreviewedDetail", { count: overflowCount })
      : "",
  ].filter(Boolean);
  const options = {
    title: deleted === 0
      ? t("deletePreview.noRecordsDeleted")
      : t("deletePreview.recordsDeleted", { count: deleted }),
    ...(details.length > 0 ? { description: details.join(". ") } : {}),
  };
  if (deleted > 0 && details.length === 0) {
    toast.success(options);
    return;
  }
  toast.warning(options);
}
