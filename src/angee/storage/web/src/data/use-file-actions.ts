import { useReconcileMutationResponse } from "@angee/refine";
import { type Row } from "@angee/metadata";
import {
  useUpdate, type BaseRecord, type HttpError, } from "@refinedev/core";
import { useBusyRun, useDeleteWithPreview, useTrashRecord } from "@angee/ui";
import {
  refineResourceName,
  useModelMetadata,
  useResourceInvalidates,
} from "@angee/metadata";
import type {
  DataResourceMetadata,
} from "@angee/metadata";

export interface FileActions {
  busy: boolean;
  /** Soft-delete a file into the Trash. */
  trash: (id: string) => Promise<void>;
  /** Pull a file back out of the Trash. */
  restore: (id: string) => Promise<void>;
  /** Move a file into a folder by public id, or `null` for the root. */
  move: (id: string, folder: string | null) => Promise<void>;
  /** Soft-delete many files in one pass, refetching once. */
  trashMany: (ids: Iterable<string>) => Promise<void>;
  /** Restore many files in one pass, refetching once. */
  restoreMany: (ids: Iterable<string>) => Promise<void>;
}

/**
 * The single-file lifecycle verbs (trash, restore) over the shared record
 * soft-delete verbs. Renames go through the record form's own update. Resource
 * and authored queries reconcile through the shared mutation owner.
 */
export function useFileActions(): FileActions {
  const metadata = useModelMetadata(FILE_MODEL);
  const resource = metadata?.resource ?? null;
  const deleteWithPreview = useDeleteWithPreview(resource);
  const trashRecord = useTrashRecord(resource);
  const reconcile = useReconcileMutationResponse();
  const invalidates = useResourceInvalidates([FILE_MODEL],
    (_data: unknown, input: { id?: string | number }) => [{ model: FILE_MODEL, id: String(input.id) }]);
  const updateFile = useUpdate<RowRecord, HttpError, Record<string, unknown>>({
    resource: refineResourceName(resource),
    dataProviderName: resource?.schemaName,
    invalidates: [],
    mutationOptions: { onSuccess: (result, input) => reconcile(result.data, input, {
      records: [{ model: FILE_MODEL, id: String(input.id) }], invalidates,
    }) },
  });
  const { busy, run } = useBusyRun();

  return {
    busy,
    trash: (id) =>
      run(async () => {
        requireFileResource(resource);
        await deleteWithPreview.remove(id);
      }),
    restore: (id) =>
      run(async () => {
        requireFileResource(resource);
        await trashRecord.restore(id);
      }),
    move: (id, folder) =>
      run(async () => {
        requireFileResource(resource);
        await updateFile.mutateAsync({ id, values: { folder } });
      }),
    trashMany: (ids) =>
      run(() => reconcile.batch(async (reconciliation) => {
        for (const id of ids) {
          requireFileResource(resource);
          await deleteWithPreview.remove(id, { reconciliation });
        }
      })),
    restoreMany: (ids) =>
      run(() => reconcile.batch(async (reconciliation) => {
        requireFileResource(resource);
        for (const id of ids) await trashRecord.restore(id, { reconciliation });
      })),
  };
}

const FILE_MODEL = "storage.File";

type RowRecord = BaseRecord & Row;

function requireFileResource(
  resource: DataResourceMetadata | null,
): asserts resource is DataResourceMetadata {
  if (!resource) {
    throw new Error(`Resource metadata for "${FILE_MODEL}" is not available.`);
  }
}
