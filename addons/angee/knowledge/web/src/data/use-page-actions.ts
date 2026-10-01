import { useCallback, useMemo } from "react";

import {
  type Row, } from "@angee/metadata";
import {
  useUpdate, type BaseRecord, type HttpError, } from "@refinedev/core";
import {
  refineFieldsFromPaths, useAuthoredMutation, } from "@angee/refine";
import {
  refineResourceName, } from "@angee/metadata";
import {
  rowPublicId, } from "@angee/metadata";
import {
  useBusyRun, useDeleteWithPreview, useLatestRef } from "@angee/ui";
import {
  useModelMetadata,
} from "@angee/metadata";
import { KnowledgeCreatePage } from "./documents";

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
 * The navigator write verbs over the knowledge mutations (create uses the
 * concrete page factory; move rides `updatePage`'s parent patch).
 * `onChanged` fires after each so the caller can refetch the tree.
 */
export function usePageActions(
  options: { onChanged?: () => void } = {},
): PageActions {
  const { onChanged } = options;
  const metadata = useModelMetadata(PAGE_MODEL);
  const resource = metadata?.resource ?? null;
  const resourceName = refineResourceName(resource);
  const fields = useMemo(() => refineFieldsFromPaths(["id", "title"]), []);
  const [createPageMutation] = useAuthoredMutation(KnowledgeCreatePage);
  const updatePageMutation = useUpdate<RowRecord, HttpError, Record<string, unknown>>({
    resource: resourceName,
    dataProviderName: resource?.schemaName,
    meta: { fields },
    invalidates: ["list", "many", "detail"],
  });
  const deleteWithPreview = useDeleteWithPreview(resource);
  const { busy, run } = useBusyRun(onChanged);

  // The navigator publishes into the shell primary pane, so its action handlers
  // must stay stable even if Refine refreshes the mutation function identities.
  const { mutateAsync: updateMutate } = updatePageMutation;
  const actionRef = useLatestRef({
    createPageMutation,
    deleteWithPreview,
    resource,
    run,
    updateMutate,
  });

  const createPage = useCallback<PageActions["createPage"]>(
    ({ vault, title, kind, parent }) => {
      const { createPageMutation, resource, run } = actionRef.current;
      return run(async () => {
        requirePageResource(resource);
        const response = await createPageMutation({ vault, title, kind, parent });
        return rowPublicId(response?.create_page ?? null);
      });
    },
    [],
  );

  const deletePage = useCallback<PageActions["deletePage"]>(
    (id) => {
      const { deleteWithPreview, resource, run } = actionRef.current;
      return run(async () => {
        requirePageResource(resource);
        await deleteWithPreview.remove(id);
      });
    },
    [],
  );

  const movePage = useCallback<PageActions["movePage"]>(
    (id, parent) => {
      const { resource, run, updateMutate } = actionRef.current;
      return run(async () => {
        requirePageResource(resource);
        await updateMutate({ id, values: { parent } });
      });
    },
    [],
  );

  return useMemo(
    () => ({ busy, createPage, deletePage, movePage }),
    [busy, createPage, deletePage, movePage],
  );
}

const PAGE_MODEL = "knowledge.Page";

type RowRecord = BaseRecord & Row;

function requirePageResource(
  resource: NonNullable<ReturnType<typeof useModelMetadata>>["resource"] | null,
): asserts resource is NonNullable<ReturnType<typeof useModelMetadata>>["resource"] {
  if (!resource) {
    throw new Error(`Resource metadata for "${PAGE_MODEL}" is not available.`);
  }
}
