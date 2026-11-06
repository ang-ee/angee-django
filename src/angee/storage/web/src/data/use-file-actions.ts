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

type DeleteOptions = Parameters<ReturnType<typeof useDeleteWithPreview>["remove"]>[1];
type MoveOptions = Pick<NonNullable<Parameters<ReturnType<typeof useUpdate<RowRecord, HttpError, Record<string, unknown>>>["mutateAsync"]>[0]>, "errorNotification">;

export interface FileActions {
  busy: boolean;
  /** Soft-delete a file into the Trash; resolves whether it was trashed. */
  trash: (id: string, options?: DeleteOptions) => Promise<boolean>;
  /** Pull a file back out of the Trash; resolves whether it was restored. */
  restore: (id: string) => Promise<boolean>;
  /** Move a file into a folder by public id, or `null` for the root. */
  move: (id: string, folder: string | null, options?: MoveOptions) => Promise<void>;
  /** Soft-delete many files in one pass, refetching once; stops at the first refusal and resolves whether all were trashed. */
  trashMany: (ids: Iterable<string>, options?: DeleteOptions) => Promise<boolean>;
  /** Restore many files in one pass, refetching once; stops at the first refusal and resolves whether all were restored. */
  restoreMany: (ids: Iterable<string>) => Promise<boolean>;
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
    trash: (id, options) =>
      run(async () => {
        requireFileResource(resource);
        return deleteWithPreview.remove(id, options);
      }),
    restore: (id) =>
      run(async () => {
        requireFileResource(resource);
        return trashRecord.restore(id);
      }),
    move: (id, folder, options) =>
      run(async () => {
        requireFileResource(resource);
        await updateFile.mutateAsync({ id, values: { folder }, ...options });
      }),
    trashMany: (ids, options) =>
      run(async () => {
        for (const id of ids) {
          requireFileResource(resource);
          if (!(await deleteWithPreview.remove(id, options))) return false;
        }
        return true;
      }),
    restoreMany: (ids) =>
      run(async () => {
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
