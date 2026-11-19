import * as React from "react";
import { useCan } from "@refinedev/core";
import {
  useAngeeDeletePreview,
  type DeletePreview,
  type DeletePreviewGroup,
  type DeletePreviewNode,
} from "@angee/refine";
import { refineResourceName, useModelMetadata, useModelRootFields } from "@angee/metadata";

import { errorMessage, useToast } from "../../feedback";
import { useUiT } from "../../i18n";
import { useDeletePreviewOperation } from "./resource-operations";
import { useRuntimeViewAs } from "../../runtime";
import { useLatestRef } from "../../lib/use-latest-ref";
import { BULK_ACTION_LIMIT, settleBulkRows, useBulkAction } from "./bulk-action";

interface BulkPreviewState {
  preview: DeletePreview;
  selectedIds: readonly string[];
  refusals: ReadonlyMap<string, string>;
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

/**
 * Preview and confirm only the bounded selection. The completion callback
 * receives successful ids; refused and overflow rows remain selected.
 * @owner ListView
 */
export function useBulkDelete(
  resource: string,
  selectedIds: ReadonlySet<string>,
  onSucceeded: (ids: readonly string[]) => void,
): UseBulkDeleteResult {
  const viewAs = useLatestRef(useRuntimeViewAs());
  const t = useUiT();
  const toast = useToast();
  const rootFields = useModelRootFields(resource);
  const metadata = useModelMetadata(resource);
  const dataResource = metadata?.resource ?? null;
  const deletePreviewOperation = useDeletePreviewOperation(dataResource);
  const refineResource = refineResourceName(dataResource);
  const deleteAccess = useCan({
    resource: refineResource,
    action: "delete",
    queryOptions: { enabled: Boolean(dataResource) },
  });
  const canDelete =
    (rootFields === null || Boolean(rootFields.deletePreview))
    && (deleteAccess.data?.can ?? true);
  const deletePreview = useAngeeDeletePreview(deletePreviewOperation.target, {
    document: deletePreviewOperation.document,
  });
  const bulk = useBulkAction(dataResource, onSucceeded);
  const mutate = React.useCallback(
    async ({ id, confirm }: { id: string; confirm?: boolean }) => {
      if (viewAs.current.viewAs || viewAs.current.pending) throw new Error(t("viewAs.readOnly"));
      if (!canDelete) {
        throw new Error(`Delete mutation for "${resource}" is disabled.`);
      }
      if (!dataResource) {
        throw new Error(`Resource metadata for "${resource}" is not available.`);
      }
      const preview = await deletePreview.mutate({ id, confirm }, { errorNotification: false });
      if (!preview) throw new Error(t("deletePreview.emptyPreview"));
      return preview;
    },
    [
      canDelete,
      dataResource,
      deletePreview.mutate,
      viewAs,
      resource,
      t,
    ],
  );
  const selectedIdList = React.useMemo(
    () => [...selectedIds],
    [selectedIds],
  );
  const [previewState, setPreviewState] =
    React.useState<BulkPreviewState | null>(null);
  const [previewPending, setPreviewPending] = React.useState(false);
  const deletePending = bulk.pending;
  const requestIdRef = React.useRef(0);

  React.useEffect(() => {
    return () => {
      requestIdRef.current += 1;
    };
  }, []);

  const deleteInitiate = React.useCallback(() => {
    if (viewAs.current.viewAs || viewAs.current.pending) return;
    if (!canDelete || selectedIdList.length === 0 || previewPending || deletePending) return;
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    const previewedIds = selectedIdList.slice(0, BULK_ACTION_LIMIT);
    const overflowCount = Math.max(
      0,
      selectedIdList.length - previewedIds.length,
    );
    setPreviewPending(true);
    void settleBulkRows(previewedIds, (id) => mutate({ id, confirm: false }))
      .then((entries) => {
        if (requestIdRef.current !== requestId) return;
        const previews: DeletePreview[] = [];
        const refusals = new Map<string, string>();
        for (const { id, result } of entries) {
          if (result.status === "rejected") {
            refusals.set(id, errorMessage(result.reason, t("deletePreview.loadError")));
          } else {
            previews.push(result.value);
            if (result.value.hasBlockers) refusals.set(id, deleteRefusal(result.value, t));
          }
        }
        const preview = aggregatePreviews(previews, overflowCount, t);
        setPreviewState({
          preview: { ...preview, hasBlockers: refusals.size > 0,
            refusals: [...new Set([...preview.refusals, ...refusals.values()])] },
          selectedIds: selectedIdList,
          refusals,
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
  }, [canDelete, deletePending, mutate, previewPending, viewAs, selectedIdList, toast, t]);

  const onCancel = React.useCallback(() => {
    if (deletePending) return;
    requestIdRef.current += 1;
    setPreviewState(null);
    setPreviewPending(false);
  }, [deletePending]);

  const onConfirm = React.useCallback(() => {
    const state = previewState;
    if (!canDelete || !state || deletePending || viewAs.current.viewAs || viewAs.current.pending) return;
    if (state.refusals.size >= Math.min(state.selectedIds.length, BULK_ACTION_LIMIT)) return;
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    void bulk.run({
      ids: state.selectedIds,
      perform: async (id) => {
        const refusal = state.refusals.get(id);
        if (refusal) throw new Error(refusal);
        const preview = await mutate({ id, confirm: true });
        if (preview.hasBlockers) throw new Error(deleteRefusal(preview, t));
      },
      done: (count) => count ? t("deletePreview.recordsDeleted", { count }) : t("deletePreview.noRecordsDeleted"),
      failed: (count) => t("deletePreview.failedDetail", { count }),
    }).finally(() => {
      if (requestIdRef.current === requestId) setPreviewState(null);
    });
  }, [bulk.run, canDelete, deletePending, mutate, viewAs, previewState, t]);

  return {
    previewState: previewState?.preview ?? null,
    previewRecordCount: Math.min(previewState?.selectedIds.length ?? 0, BULK_ACTION_LIMIT),
    previewOverflowCount: previewState?.overflowCount ?? 0,
    previewBlockedRecordCount: previewState?.refusals.size ?? 0,
    isPreviewOpen: previewState !== null,
    isPending: previewPending || deletePending,
    canDelete,
    deleteInitiate,
    onConfirm,
    onCancel,
  };
}

function aggregatePreviews(
  entries: readonly DeletePreview[],
  overflowCount: number,
  t: ReturnType<typeof useUiT>,
): DeletePreview {
  const roots = entries.map((entry) => entry.root);
  return {
    totalDeletedCount: entries.reduce(
      (total, entry) => total + entry.totalDeletedCount,
      0,
    ),
    deleted: aggregateGroups(entries.flatMap((entry) => entry.deleted)),
    updated: aggregateGroups(entries.flatMap((entry) => entry.updated)),
    blocked: aggregateGroups(entries.flatMap((entry) => entry.blocked)),
    refusals: [...new Set(entries.flatMap((entry) => entry.refusals))],
    hasBlockers: entries.some((entry) => entry.hasBlockers),
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

function deleteRefusal(preview: DeletePreview, t: ReturnType<typeof useUiT>): string {
  return preview.refusals[0] || preview.blocked[0]?.label || t("deletePreview.blockedGeneric");
}
