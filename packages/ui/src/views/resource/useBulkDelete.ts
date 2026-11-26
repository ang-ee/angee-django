import * as React from "react";
import { useCan } from "@refinedev/core";
import {
  useAngeeDeletePreview,
  type DeletePreview,
  type DeletePreviewGroup,
  type DeletePreviewNode,
} from "@angee/refine";
import { refineResourceName, useModelMetadata, resourceLifecycle, type Row } from "@angee/metadata";

import { errorMessage, useToast } from "../../feedback";
import { SURFACE_RENDERS_FAILURE } from "../form/validation-errors";
import { useUiT } from "../../i18n";
import { useDeletePreviewOperation } from "./resource-operations";
import { useRuntimeViewAs } from "../../runtime";
import { useLatestRef } from "../../lib/use-latest-ref";
import { BULK_ACTION_FANOUT_LIMIT, settleBulkRows, type BulkActionRunner } from "./bulk-action";

interface BulkPreviewState {
  preview: DeletePreview;
  selectedIds: readonly string[];
  blockedCount: number;
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
 * Preview and confirm only the bounded selection through the surface's shared
 * runner. Its completion callback receives successful ids; refused, failed and
 * overflow rows remain selected. The runner stays reserved through confirmation.
 * @owner ListView
 */
export function useBulkDelete(
  resource: string,
  selectedIds: ReadonlySet<string>,
  bulk: BulkActionRunner,
  /** Selection snapshots; a record form checks its own loaded row before exposing Delete. */
  selectedRows?: readonly Row[],
): UseBulkDeleteResult {
  const viewAs = useLatestRef(useRuntimeViewAs());
  const t = useUiT();
  const toast = useToast();
  const metadata = useModelMetadata(resource);
  const dataResource = metadata?.resource ?? null;
  // Delete is gated by the resource's delete-preview root; the server authorizes each
  // row. A type's `permissions` field projects only the names it declares, and list
  // rows do not select it, so a row cannot be refused here for lacking `delete`.
  const selectedIdList = React.useMemo(() => [...selectedIds], [selectedIds]);
  const deletePreviewOperation = useDeletePreviewOperation(dataResource);
  const refineResource = refineResourceName(dataResource);
  const deleteAccess = useCan({
    resource: refineResource,
    action: "delete",
    queryOptions: { enabled: Boolean(dataResource) },
  });
  const canDelete =
    resourceLifecycle(dataResource, metadata).canDelete
    && (deleteAccess.data?.can ?? true)
    && (selectedRows === undefined || selectedIdList.length > 0);
  const deletePreview = useAngeeDeletePreview(deletePreviewOperation.target, {
    document: deletePreviewOperation.document,
  });
  const mutate = React.useCallback(
    async ({ id, confirm }: { id: string; confirm?: boolean }) => {
      if (viewAs.current.viewAs || viewAs.current.pending) throw new Error(t("viewAs.readOnly"));
      if (!canDelete) {
        throw new Error(`Delete mutation for "${resource}" is disabled.`);
      }
      if (!dataResource) {
        throw new Error(`Resource metadata for "${resource}" is not available.`);
      }
      const preview = await deletePreview.mutate({ id, confirm }, SURFACE_RENDERS_FAILURE);
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
  const [previewState, setPreviewState] =
    React.useState<BulkPreviewState | null>(null);
  const [previewPending, setPreviewPending] = React.useState(false);
  const [deletePending, setDeletePending] = React.useState(false);
  const requestIdRef = React.useRef(0);
  const confirmation = React.useRef<((confirmed: boolean) => void) | null>(null);

  React.useEffect(() => {
    return () => {
      requestIdRef.current += 1;
      confirmation.current?.(false);
      confirmation.current = null;
    };
  }, []);

  const deleteInitiate = React.useCallback(() => {
    if (viewAs.current.viewAs || viewAs.current.pending) return;
    if (!canDelete || selectedIdList.length === 0 || bulk.pending) return;
    let requestId: number | undefined;
    const previewedIds = selectedIdList.slice(0, BULK_ACTION_FANOUT_LIMIT);
    const overflowCount = Math.max(
      0,
      selectedIdList.length - previewedIds.length,
    );
    const refusals = new Map<string, string>();
    const failures = new Map<string, string>();
    void bulk.run({
      ids: selectedIdList,
      confirm: async () => {
        requestId = ++requestIdRef.current;
        setPreviewPending(true);
        const entries = await settleBulkRows(previewedIds, (id) => mutate({ id, confirm: false }));
        if (requestIdRef.current !== requestId) return false;
        const previews: DeletePreview[] = [];
        for (const { id, result } of entries) {
          if (result.status === "rejected") {
            failures.set(id, errorMessage(result.reason, t("deletePreview.loadError")));
          } else {
            previews.push(result.value);
            if (result.value.hasBlockers) refusals.set(id, deleteRefusal(result.value, t));
          }
        }
        const preview = aggregatePreviews(previews, overflowCount, t);
        const blockedCount = refusals.size + failures.size;
        setPreviewState({
          preview: { ...preview, hasBlockers: blockedCount > 0,
            refusals: [...new Set([...preview.refusals, ...refusals.values(), ...failures.values()])] },
          selectedIds: selectedIdList,
          blockedCount,
          overflowCount,
        });
        setPreviewPending(false);
        return new Promise<boolean>((resolve) => { confirmation.current = resolve; });
      },
      perform: async (id) => {
        const failure = failures.get(id);
        if (failure) throw new Error(failure);
        const refusal = refusals.get(id);
        if (refusal) return { status: "refused", reason: refusal };
        const preview = await mutate({ id, confirm: true });
        if (preview.hasBlockers) return { status: "refused", reason: deleteRefusal(preview, t) };
      },
      done: (count) => t("deletePreview.recordsDeleted", { count }),
      failed: () => t("deletePreview.noRecordsDeleted"),
    })
      .catch((error: unknown) => {
        if (requestIdRef.current !== requestId) return;
        toast.danger({
          title: t("deletePreview.failedTitle"),
          description: errorMessage(
            error,
            t("deletePreview.loadError"),
          ),
        });
      })
      .finally(() => {
        if (requestIdRef.current !== requestId) return;
        confirmation.current = null;
        setPreviewState(null);
        setPreviewPending(false);
        setDeletePending(false);
      });
  }, [bulk.pending, bulk.run, canDelete, mutate, viewAs, selectedIdList, toast, t]);

  const onCancel = React.useCallback(() => {
    if (deletePending) return;
    confirmation.current?.(false);
    confirmation.current = null;
  }, [deletePending]);

  const onConfirm = React.useCallback(() => {
    const state = previewState;
    if (!canDelete || !state || deletePending || viewAs.current.viewAs || viewAs.current.pending) return;
    if (state.blockedCount >= Math.min(state.selectedIds.length, BULK_ACTION_FANOUT_LIMIT) || !confirmation.current) return;
    setDeletePending(true);
    confirmation.current(true);
    confirmation.current = null;
  }, [canDelete, deletePending, viewAs, previewState]);

  return {
    previewState: previewState?.preview ?? null,
    previewRecordCount: Math.min(previewState?.selectedIds.length ?? 0, BULK_ACTION_FANOUT_LIMIT),
    previewOverflowCount: previewState?.overflowCount ?? 0,
    previewBlockedRecordCount: previewState?.blockedCount ?? 0,
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
