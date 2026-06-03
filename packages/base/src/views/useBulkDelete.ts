import * as React from "react";
import {
  useResourceMutation,
  type DeletePreview,
  type DeletePreviewGroup,
  type DeletePreviewNode,
} from "@angee/sdk";

import { useToast } from "../feedback";

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
  deleteInitiate: () => void;
  onConfirm: () => void;
  onCancel: () => void;
}

export function useBulkDelete(
  model: string,
  selectedIds: ReadonlySet<string>,
  clearSelectedIds: () => void,
): UseBulkDeleteResult {
  const toast = useToast();
  const [mutate] = useResourceMutation(model, "delete");
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
    if (selectedIdList.length === 0) return;
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
          toast.error({
            title: "Delete preview failed",
            description: "No preview was returned for the selected records.",
          });
          return;
        }
        const blockedIds = previews
          .filter((entry) => entry.preview.hasBlockers)
          .map((entry) => entry.id);
        setPreviewState({
          preview: aggregatePreviews(previews, overflowCount),
          selectedIds: selectedIdList,
          blockedIds,
          overflowCount,
        });
      })
      .catch((error: unknown) => {
        toast.error({
          title: "Delete preview failed",
          description: errorMessage(error),
        });
      })
      .finally(() => {
        if (requestIdRef.current === requestId) setPreviewPending(false);
      });
  }, [mutate, selectedIdList, toast]);

  const onCancel = React.useCallback(() => {
    if (deletePending) return;
    requestIdRef.current += 1;
    setPreviewState(null);
    setPreviewPending(false);
  }, [deletePending]);

  const onConfirm = React.useCallback(() => {
    const state = previewState;
    if (!state || deletePending) return;
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
        });
      })
      .finally(() => {
        if (requestIdRef.current === requestId) setDeletePending(false);
      });
  }, [clearSelectedIds, deletePending, mutate, previewState, toast]);

  return {
    previewState: previewState?.preview ?? null,
    previewRecordCount: previewState?.selectedIds.length ?? 0,
    previewOverflowCount: previewState?.overflowCount ?? 0,
    previewBlockedRecordCount: previewState?.blockedIds.length ?? 0,
    isPreviewOpen: previewState !== null,
    isPending: previewPending || deletePending,
    deleteInitiate,
    onConfirm,
    onCancel,
  };
}

function aggregatePreviews(
  entries: readonly PreviewEntry[],
  overflowCount: number,
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
    root: aggregateRoot(roots, overflowCount),
  };
}

function aggregateRoot(
  roots: readonly DeletePreviewNode[],
  overflowCount: number,
): DeletePreviewNode {
  return {
    label: "Selection",
    objectLabel: `${roots.length} records`,
    objectId: null,
    children: overflowCount > 0
      ? [...roots, moreNode(overflowCount)]
      : roots,
  };
}

function moreNode(count: number): DeletePreviewNode {
  return {
    label: "",
    objectLabel: `+${count} more`,
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
}: {
  toast: ReturnType<typeof useToast>;
  deleted: number;
  blocked: number;
  failed: number;
  overflowCount: number;
}) {
  const details = [
    blocked > 0 ? `${blocked} blocked` : "",
    failed > 0 ? `${failed} failed` : "",
    overflowCount > 0 ? `${overflowCount} were not previewed` : "",
  ].filter(Boolean);
  const options = {
    title: deleted === 0 ? "No records deleted" : `Deleted ${deleted} records`,
    ...(details.length > 0 ? { description: details.join(". ") } : {}),
  };
  if (deleted > 0 && details.length === 0) {
    toast.success(options);
    return;
  }
  toast.warning(options);
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "The delete preview could not be loaded.";
}
