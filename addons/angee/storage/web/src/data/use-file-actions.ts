import { useAuthoredMutation, useBusyRun } from "@angee/sdk";

import {
  StorageDeleteFile,
  StorageRestoreFile,
  StorageUpdateFile,
} from "./documents";

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
 * The single-file lifecycle verbs (trash, restore) over the storage soft-delete
 * mutations. Renames go through the record form's own update; `onChanged` fires
 * after each verb so the caller can refetch.
 */
export function useFileActions(
  options: { onChanged?: () => void } = {},
): FileActions {
  const { onChanged } = options;
  const [deleteFile] = useAuthoredMutation(StorageDeleteFile);
  const [restoreFile] = useAuthoredMutation(StorageRestoreFile);
  const [updateFile] = useAuthoredMutation(StorageUpdateFile);
  const { busy, run } = useBusyRun(onChanged);

  return {
    busy,
    trash: (id) =>
      run(async () => {
        await deleteFile({ id });
      }),
    restore: (id) =>
      run(async () => {
        await restoreFile({ id });
      }),
    move: (id, folder) =>
      run(async () => {
        await updateFile({ data: { id, folder } });
      }),
    trashMany: (ids) =>
      run(async () => {
        for (const id of ids) await deleteFile({ id });
      }),
    restoreMany: (ids) =>
      run(async () => {
        for (const id of ids) await restoreFile({ id });
      }),
  };
}
