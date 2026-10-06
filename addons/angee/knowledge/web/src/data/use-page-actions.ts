import { useCallback, useMemo } from "react";

import {
  type Row, } from "@angee/metadata";
import {
  useUpdate, type BaseRecord, type HttpError, } from "@refinedev/core";
import {
  refineFieldsFromPaths, useAuthoredMutation, useInvalidateAuthoredModels, } from "@angee/refine";
import {
  refineResourceName, } from "@angee/metadata";
import {
  rowPublicId, } from "@angee/metadata";
import {
  useBusyRun, useLatestRef, useTrashRecord } from "@angee/ui";
import {
  useModelMetadata,
} from "@angee/metadata";
import { KnowledgeCreatePage } from "./documents";

import { PAGE_MODEL } from "./documents";

export interface PageActions {
  busy: boolean;
  /** Create a page in a vault, optionally under a parent; returns its node id. */
  createPage: (input: {
    vault: string;
    title: string;
    kind: string;
    parent: string | null;
  }) => Promise<string | null>;
  /** Confirm, then move a page (and the pages below it) to the trash; resolves whether it moved. */
  trashPage: (id: string, title: string) => Promise<boolean>;
  /** Restore a trashed page with the pages trashed along with it. */
  restorePage: (id: string) => Promise<boolean>;
  /** Reparent a page (move) — `null` lifts it to the vault root. */
  movePage: (id: string, parent: string | null) => Promise<void>;
}

/**
 * The navigator write verbs over the knowledge mutations (create is the gated
 * factory mutation; trash and restore are the shared record verbs; move rides
 * `updatePage`'s parent patch). Successful writes invalidate the shared
 * authored page reads.
 */
export function usePageActions(): PageActions {
  const invalidateModels = useInvalidateAuthoredModels();
  const invalidatePages = useCallback(() => invalidateModels([PAGE_MODEL]), [invalidateModels]);
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
  const trashRecord = useTrashRecord(resource);
  const { busy, run } = useBusyRun(invalidatePages);

  // The navigator publishes into the shell primary pane, so its action handlers
  // must stay stable even if Refine refreshes the mutation function identities.
  const { mutateAsync: updateMutate } = updatePageMutation;
  const actionRef = useLatestRef({
    createPageMutation,
    resource,
    run,
    trashRecord,
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

  const trashPage = useCallback<PageActions["trashPage"]>(
    (id, title) => {
      const { resource, run, trashRecord } = actionRef.current;
      return run(async () => {
        requirePageResource(resource);
        return await trashRecord.trash(id, title);
      });
    },
    [],
  );

  const restorePage = useCallback<PageActions["restorePage"]>(
    (id) => {
      const { resource, run, trashRecord } = actionRef.current;
      return run(async () => {
        requirePageResource(resource);
        return await trashRecord.restore(id);
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
    () => ({ busy: busy || trashRecord.busy, createPage, trashPage, restorePage, movePage }),
    [busy, createPage, trashPage, restorePage, movePage, trashRecord.busy],
  );
}

type RowRecord = BaseRecord & Row;

function requirePageResource(
  resource: NonNullable<ReturnType<typeof useModelMetadata>>["resource"] | null,
): asserts resource is NonNullable<ReturnType<typeof useModelMetadata>>["resource"] {
  if (!resource) {
    throw new Error(`Resource metadata for "${PAGE_MODEL}" is not available.`);
  }
}
