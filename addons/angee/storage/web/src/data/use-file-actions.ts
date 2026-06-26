import {
  resourceOperationTarget,
  type Row,
} from "@angee/resources";
import {
  useCustomMutation,
  useInvalidate,
  useUpdate,
  type BaseRecord,
  type HttpError,
  } from "@refinedev/core";
import {
  deletePreviewDocumentForResource,
  deletePreviewRequest,
  extractDeletePreview,
  useOperationDocuments,
  type DeletePreviewVariables,
  } from "@angee/refine";
import {
  useAuthoredMutation,
  useBusyRun,
} from "@angee/ui";
import {
  refineResourceName,
  useModelMetadata,
} from "@angee/resources";
import type {
  DataResourceMetadata,
} from "@angee/resources";

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
  const deleteFile =
    useCustomMutation<BaseRecord, HttpError, DeletePreviewVariables>();
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
          deleteFile,
          invalidate,
          operationDocuments,
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
            deleteFile,
            invalidate,
            operationDocuments,
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
  deleteFile,
  invalidate,
  operationDocuments,
  resource,
  id,
}: {
  deleteFile: ReturnType<typeof useCustomMutation<BaseRecord, HttpError, DeletePreviewVariables>>;
  invalidate: ReturnType<typeof useInvalidate>;
  operationDocuments: ReturnType<typeof useOperationDocuments>;
  resource: DataResourceMetadata | null;
  id: string;
}): Promise<void> {
  requireFileResource(resource);
  const request = deletePreviewRequest(
    resourceOperationTarget(resource, "deletePreview"),
    { id, confirm: true },
    {
      document: deletePreviewDocumentForResource(
        operationDocuments,
        resource.schemaName,
        resource.modelLabel,
      ),
    },
  );
  const response = await deleteFile.mutateAsync({
    url: "",
    method: "post",
    values: { id, confirm: true },
    dataProviderName: request.dataProviderName,
    meta: request.meta,
  });
  void extractDeletePreview(response.data, request.root);
  await invalidate({
    resource: refineResourceName(resource),
    dataProviderName: request.dataProviderName,
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
