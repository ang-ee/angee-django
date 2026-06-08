import { useCallback, useState } from "react";

import { useAuthoredMutation } from "@angee/sdk";

import {
  FILE_DELETE_MUTATION,
  FILE_RESTORE_MUTATION,
  FILE_UPDATE_MUTATION,
  type FileDeleteData,
  type FileIdVariables,
  type FileRestoreData,
  type FileUpdateData,
  type FileUpdateVariables,
} from "./documents";

export interface FileActions {
  busy: boolean;
  /** Soft-delete a file into the Trash. */
  trash: (id: string) => Promise<void>;
  /** Pull a file back out of the Trash. */
  restore: (id: string) => Promise<void>;
  /** Move a file into a folder (a folder GlobalID), or `null` for the root. */
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
  const [deleteFile] = useAuthoredMutation<FileDeleteData, FileIdVariables>(
    FILE_DELETE_MUTATION,
  );
  const [restoreFile] = useAuthoredMutation<FileRestoreData, FileIdVariables>(
    FILE_RESTORE_MUTATION,
  );
  const [updateFile] = useAuthoredMutation<FileUpdateData, FileUpdateVariables>(
    FILE_UPDATE_MUTATION,
  );
  const [busy, setBusy] = useState(false);

  const run = useCallback(
    async (action: () => Promise<unknown>): Promise<void> => {
      setBusy(true);
      try {
        await action();
        onChanged?.();
      } finally {
        setBusy(false);
      }
    },
    [onChanged],
  );

  return {
    busy,
    trash: (id) => run(() => deleteFile({ id })),
    restore: (id) => run(() => restoreFile({ id })),
    move: (id, folder) => run(() => updateFile({ data: { id, folder } })),
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
