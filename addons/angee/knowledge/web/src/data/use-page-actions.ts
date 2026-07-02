import { useCallback, useMemo } from "react";

import {
  resourceOperationTarget, type Row, } from "@angee/metadata";
import {
  useCreate, useInvalidate, useUpdate, type BaseRecord, type HttpError, } from "@refinedev/core";
import {
  refineFieldsFromPaths, } from "@angee/refine";
import {
  deletePreviewDocumentForResource, useAngeeDeletePreview, useOperationDocuments, } from "@angee/refine";
import {
  refineResourceName, } from "@angee/metadata";
import {
  rowPublicId, } from "@angee/metadata";
import {
  useBusyRun, useLatestRef } from "@angee/ui";
import {
  useModelMetadata,
} from "@angee/metadata";

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
  const metadata = useModelMetadata(PAGE_MODEL);
  const resource = metadata?.resource ?? null;
  const operationDocuments = useOperationDocuments();
  const resourceName = resource ? refineResourceName(resource) : "";
  const fields = useMemo(() => refineFieldsFromPaths(["id", "title"]), []);
  const createPageMutation = useCreate<RowRecord, HttpError, Record<string, unknown>>({
    resource: resourceName,
    dataProviderName: resource?.schemaName,
    meta: { fields },
    invalidates: ["list", "many"],
  });
  const updatePageMutation = useUpdate<RowRecord, HttpError, Record<string, unknown>>({
    resource: resourceName,
    dataProviderName: resource?.schemaName,
    meta: { fields },
    invalidates: ["list", "many", "detail"],
  });
  const deletePreviewTarget = resource
    ? resourceOperationTarget(resource, "deletePreview")
    : null;
  const deletePreviewDocument = resource
    ? deletePreviewDocumentForResource(
        operationDocuments,
        resource.schemaName,
        resource.modelLabel,
      )
    : "";
  const deletePreview = useAngeeDeletePreview(deletePreviewTarget, {
    document: deletePreviewDocument,
  });
  const invalidate = useInvalidate();
  const { busy, run } = useBusyRun(onChanged);

  // The navigator publishes into the shell primary pane, so its action handlers
  // must stay stable even if Refine refreshes the mutation function identities.
  const { mutateAsync: createMutate } = createPageMutation;
  const { mutateAsync: updateMutate } = updatePageMutation;
  const actionRef = useLatestRef({
    createMutate,
    deletePreview,
    invalidate,
    resource,
    run,
    updateMutate,
  });

  const createPage = useCallback<PageActions["createPage"]>(
    ({ vault, title, kind, parent }) => {
      const { createMutate, resource, run } = actionRef.current;
      return run(async () => {
        requirePageResource(resource);
        const response = await createMutate({
          values: { vault, title, kind, parent },
        });
        return rowPublicId(response.data ?? null);
      });
    },
    [],
  );

  const deletePage = useCallback<PageActions["deletePage"]>(
    (id) => {
      const { deletePreview, invalidate, resource, run } = actionRef.current;
      return run(async () => {
        requirePageResource(resource);
        await deletePreview.mutate({ id, confirm: true });
        await invalidate({
          resource: refineResourceName(resource),
          dataProviderName: resource.schemaName,
          id,
          invalidates: ["list", "many", "detail"],
        });
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
