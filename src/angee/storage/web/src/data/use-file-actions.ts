import { type Row } from "@angee/metadata";
import {
  useUpdate, type BaseRecord, type HttpError, } from "@refinedev/core";
import { useDeleteWithPreview, useTrashRecord } from "@angee/ui";
import {
  refineResourceName,
  useModelMetadata,
} from "@angee/metadata";
import type {
  DataResourceMetadata,
} from "@angee/metadata";

import { useStorageActionRun } from "./use-action-run";

export interface FileActions {
  busy: boolean;
  /** Soft-delete a file into the Trash; resolves whether it was trashed. */
  trash: (id: string) => Promise<boolean>;
  /** Pull a file back out of the Trash; resolves whether it was restored. */
  restore: (id: string) => Promise<boolean>;
  /** Move a file into a folder by public id, or `null` for the root; resolves whether it moved. */
  move: (id: string, folder: string | null) => Promise<boolean>;
  /** Soft-delete many files in one pass, refetching once; stops at the first refusal and resolves whether all were trashed. */
  trashMany: (ids: Iterable<string>) => Promise<boolean>;
  /** Restore many files in one pass, refetching once; stops at the first refusal and resolves whether all were restored. */
  restoreMany: (ids: Iterable<string>) => Promise<boolean>;
}

/**
 * The single-file lifecycle verbs (trash, restore, move) over the shared record
 * soft-delete verbs. Every verb resolves whether it completed and settles its
 * failures through `useStorageActionRun`, the verbs' one error surface. Renames
 * go through the record form's own update. Resource caches invalidate in the
 * owners; `onChanged` lets a caller refresh a separate authored view.
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
  const { busy, settle } = useStorageActionRun(onChanged);

  return {
    busy,
    trash: (id) =>
      settle(async () => {
        requireFileResource(resource);
        return deleteWithPreview.remove(id, { errorNotification: false });
      }),
    restore: (id) =>
      settle(async () => {
        requireFileResource(resource);
        return trashRecord.restore(id);
      }),
    move: (id, folder) =>
      settle(async () => {
        requireFileResource(resource);
        await updateFile.mutateAsync({ id, values: { folder }, errorNotification: false });
        return true;
      }),
    trashMany: (ids) =>
      settle(async () => {
        for (const id of ids) {
          requireFileResource(resource);
          if (!(await deleteWithPreview.remove(id, { errorNotification: false }))) return false;
        }
        return true;
      }),
    restoreMany: (ids) =>
      settle(async () => {
        requireFileResource(resource);
        for (const id of ids) {
          if (!(await trashRecord.restore(id))) return false;
        }
        return true;
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
