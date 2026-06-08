import { useCallback, useState } from "react";

import { useAuthoredMutation } from "@angee/sdk";

import {
  CREATE_PAGE_MUTATION,
  CREATE_VAULT_MUTATION,
  DELETE_PAGE_MUTATION,
  UPDATE_PAGE_MUTATION,
  type CreatePageData,
  type CreatePageVariables,
  type CreateVaultData,
  type CreateVaultVariables,
  type DeletePageData,
  type DeletePageVariables,
  type UpdatePageData,
  type UpdatePageVariables,
} from "./documents";

export interface PageActions {
  busy: boolean;
  /** Create a page in a vault, optionally under a parent; returns its node id. */
  createPage: (input: {
    vault: string;
    title: string;
    kind: string;
    parent: string | null;
  }) => Promise<string | null>;
  /** Create a vault owned by the actor; returns its node id. */
  createVault: (name: string) => Promise<string | null>;
  /** Delete a page (and its subtree). */
  deletePage: (id: string) => Promise<void>;
  /** Reparent a page (move) — `null` lifts it to the vault root. */
  movePage: (id: string, parent: string | null) => Promise<void>;
}

/**
 * The navigator write verbs over the knowledge CRUD mutations (create/delete are
 * the gated factory mutations; move rides `updatePage`'s parent patch).
 * `onChanged` fires after each so the caller can refetch the tree.
 */
export function usePageActions(
  options: { onChanged?: () => void } = {},
): PageActions {
  const { onChanged } = options;
  const [createPageMutation] = useAuthoredMutation<
    CreatePageData,
    CreatePageVariables
  >(CREATE_PAGE_MUTATION);
  const [createVaultMutation] = useAuthoredMutation<
    CreateVaultData,
    CreateVaultVariables
  >(CREATE_VAULT_MUTATION);
  const [deletePageMutation] = useAuthoredMutation<
    DeletePageData,
    DeletePageVariables
  >(DELETE_PAGE_MUTATION);
  const [updatePageMutation] = useAuthoredMutation<
    UpdatePageData,
    UpdatePageVariables
  >(UPDATE_PAGE_MUTATION);
  const [busy, setBusy] = useState(false);

  const run = useCallback(
    async <T>(action: () => Promise<T>): Promise<T> => {
      setBusy(true);
      try {
        const result = await action();
        onChanged?.();
        return result;
      } finally {
        setBusy(false);
      }
    },
    [onChanged],
  );

  return {
    busy,
    createPage: ({ vault, title, kind, parent }) =>
      run(async () => {
        const data = await createPageMutation({
          data: { vault, title, kind, parent },
        });
        return data?.createPage.id ?? null;
      }),
    createVault: (name) =>
      run(async () => {
        const data = await createVaultMutation({ data: { name } });
        return data?.createVault.id ?? null;
      }),
    deletePage: (id) =>
      run(async () => {
        await deletePageMutation({ id });
      }),
    movePage: (id, parent) =>
      run(async () => {
        await updatePageMutation({ data: { id, parent } });
      }),
  };
}
