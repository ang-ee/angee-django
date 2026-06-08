import { useCallback, useState } from "react";

import { useAuthoredMutation } from "@angee/sdk";

import {
  CREATE_FOLDER_MUTATION,
  type CreateFolderData,
  type CreateFolderVariables,
} from "./documents";

export interface FolderActions {
  busy: boolean;
  /** Create a folder in a drive, optionally nested under a parent folder. */
  create: (input: {
    drive: string;
    name: string;
    parent: string | null;
  }) => Promise<void>;
}

/**
 * Folder write verbs over the gated `createFolder` mutation; `onChanged` fires
 * after a successful create so the navigator can refetch its tree.
 */
export function useFolderActions(
  options: { onChanged?: () => void } = {},
): FolderActions {
  const { onChanged } = options;
  const [createFolder] = useAuthoredMutation<
    CreateFolderData,
    CreateFolderVariables
  >(CREATE_FOLDER_MUTATION);
  const [busy, setBusy] = useState(false);

  const create = useCallback<FolderActions["create"]>(
    async ({ drive, name, parent }) => {
      setBusy(true);
      try {
        await createFolder({ data: { drive, name, parent } });
        onChanged?.();
      } finally {
        setBusy(false);
      }
    },
    [createFolder, onChanged],
  );

  return { busy, create };
}
