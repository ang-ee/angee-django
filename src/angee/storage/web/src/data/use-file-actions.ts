import { type Row } from "@angee/metadata";
import {
  useUpdate, type BaseRecord, type HttpError, } from "@refinedev/core";
import { useBusyRun, useDeleteWithPreview, useTrashRecord } from "@angee/ui";
import {
  refineResourceName,
  useModelMetadata,
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
 * caches invalidate in the owners; `onChanged` lets a caller refresh a separate
 * authored view.
 */
export function useFileActions(
  options: { onChanged?: () => void } = {},
): FileActions {
  const { onChanged } = options;
  const metadata = useModelMetadata(FILE_MODEL);
  const resource = metadata?.resource ?? null;
  const deleteWithPreview = useDeleteWithPreview(resource);
  const trashRecord = useTrashRecord(resource);
  const updateFile = useUpdate<RowRecord, HttpError, Record<string, unknown>>({
    resource: refineResourceName(resource),
    dataProviderName: resource?.schemaName,
    invalidates: ["list", "many", "detail"],
  });
  const { busy, run } = useBusyRun(onChanged);

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
      run(async () => {
        for (const id of ids) {
          requireFileResource(resource);
          await deleteWithPreview.remove(id);
        }
      }),
    restoreMany: (ids) =>
      run(async () => {
        requireFileResource(resource);
        for (const id of ids) await trashRecord.restore(id);
      }),
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
