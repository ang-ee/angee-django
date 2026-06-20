import { useBusyRun, useResourceMutation } from "@angee/sdk";

export interface FolderActions {
  busy: boolean;
  /** Create a folder in a drive, optionally nested under a parent folder. */
  create: (input: {
    drive: string;
    name: string;
    parent: string | null;
  }) => Promise<void>;
  /** Rename a folder. */
  rename: (id: string, name: string) => Promise<void>;
  /** Delete a folder; its files fall back to the drive root. */
  remove: (id: string) => Promise<void>;
}

/**
 * Folder write verbs over the gated `createFolder` and the folder CRUD
 * mutations; `onChanged` fires after each so the navigator can refetch its tree.
 */
export function useFolderActions(
  options: { onChanged?: () => void } = {},
): FolderActions {
  const { onChanged } = options;
  const [createFolder] = useResourceMutation("storage.Folder", "create", {
    fields: ["name"],
  });
  const [updateFolder] = useResourceMutation("storage.Folder", "update", {
    fields: ["name"],
  });
  const [deleteFolder] = useResourceMutation("storage.Folder", "delete");
  const { busy, run } = useBusyRun(onChanged);

  return {
    busy,
    create: ({ drive, name, parent }) =>
      run(async () => {
        await createFolder({ data: { drive, name, parent } });
      }),
    rename: (id, name) =>
      run(async () => {
        await updateFolder({ data: { id, name } });
      }),
    remove: (id) =>
      run(async () => {
        await deleteFolder({ id, confirm: true });
      }),
  };
}
