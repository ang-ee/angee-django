import { resourceOperationTarget, type Row, } from "@angee/metadata";
import {
  useInvalidate, useUpdate, type BaseRecord, type HttpError, } from "@refinedev/core";
import {
  deletePreviewDocumentForResource, useAngeeDeletePreview, useAuthoredMutation, useOperationDocuments, type UseAngeeDeletePreviewResult, } from "@angee/refine";
import {
  useBusyRun } from "@angee/ui";
import {
  refineResourceName,
  useModelMetadata,
} from "@angee/metadata";
import type {
  DataResourceMetadata,
} from "@angee/metadata";

import { StorageRestoreFile } from "./documents";

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
  const metadata = useModelMetadata(FILE_MODEL);
  const resource = metadata?.resource ?? null;
  const operationDocuments = useOperationDocuments();
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
  const [restoreFile] = useAuthoredMutation(StorageRestoreFile);
  const updateFile = useUpdate<RowRecord, HttpError, Record<string, unknown>>({
    resource: resource ? refineResourceName(resource) : "",
    dataProviderName: resource?.schemaName,
    invalidates: ["list", "many", "detail"],
  });
  const invalidate = useInvalidate();
  const { busy, run } = useBusyRun(onChanged);

  return {
    busy,
    trash: (id) =>
      run(async () => {
        await trashFile({
          deletePreview,
          invalidate,
          resource,
          id,
        });
      }),
    restore: (id) =>
      run(async () => {
        await restoreFile({ id });
      }),
    move: (id, folder) =>
      run(async () => {
        requireFileResource(resource);
        await updateFile.mutateAsync({ id, values: { folder } });
      }),
    trashMany: (ids) =>
      run(async () => {
        for (const id of ids) {
          await trashFile({
            deletePreview,
            invalidate,
            resource,
            id,
          });
        }
      }),
    restoreMany: (ids) =>
      run(async () => {
        for (const id of ids) await restoreFile({ id });
      }),
  };
}

const FILE_MODEL = "storage.File";

type RowRecord = BaseRecord & Row;

async function trashFile({
  deletePreview,
  invalidate,
  resource,
  id,
}: {
  deletePreview: UseAngeeDeletePreviewResult;
  invalidate: ReturnType<typeof useInvalidate>;
  resource: DataResourceMetadata | null;
  id: string;
}): Promise<void> {
  requireFileResource(resource);
  await deletePreview.mutate({ id, confirm: true });
  await invalidate({
    resource: refineResourceName(resource),
    dataProviderName: resource.schemaName,
    id,
    invalidates: ["list", "many", "detail"],
  });
}

function requireFileResource(
  resource: DataResourceMetadata | null,
): asserts resource is DataResourceMetadata {
  if (!resource) {
    throw new Error(`Resource metadata for "${FILE_MODEL}" is not available.`);
  }
}
