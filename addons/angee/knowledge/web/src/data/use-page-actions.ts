import { rowPublicId, useBusyRun, useResourceMutation } from "@angee/sdk";

export interface PageActions {
  busy: boolean;
  /** Create a page in a vault, optionally under a parent; returns its node id. */
  createPage: (input: {
    vault: string;
    title: string;
    kind: string;
    parent: string | null;
  }) => Promise<string | null>;
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
  const [createPageMutation] = useResourceMutation("knowledge.Page", "create", {
    fields: ["title"],
  });
  const [deletePageMutation] = useResourceMutation("knowledge.Page", "delete");
  const [updatePageMutation] = useResourceMutation("knowledge.Page", "update", {
    fields: ["title"],
  });
  const { busy, run } = useBusyRun(onChanged);

  return {
    busy,
    createPage: ({ vault, title, kind, parent }) =>
      run(async () => {
        const record = await createPageMutation({
          data: { vault, title, kind, parent },
        });
        return rowPublicId(record);
      }),
    deletePage: (id) =>
      run(async () => {
        await deletePageMutation({ id, confirm: true });
      }),
    movePage: (id, parent) =>
      run(async () => {
        await updatePageMutation({ data: { id, parent } });
      }),
  };
}
