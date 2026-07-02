import { resourceOperationTarget, type Row, } from "@angee/metadata";
import {
  useCreate, useInvalidate, useUpdate, type BaseRecord, type HttpError, } from "@refinedev/core";
import {
  refineFieldsFromPaths, } from "@angee/refine";
import {
  deletePreviewDocumentForResource, useAngeeDeletePreview, useOperationDocuments, } from "@angee/refine";
import {
  refineResourceName, } from "@angee/metadata";
import { useBusyRun } from "@angee/ui";
import {
  useModelMetadata,
} from "@angee/metadata";

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
 * Folder write verbs over the Hasura resource mutations; folder creation is
 * backed by the storage manager factory server-side. `onChanged` fires after
 * each so the navigator can refetch its tree.
 */
export function useFolderActions(
  options: { onChanged?: () => void } = {},
): FolderActions {
  const { onChanged } = options;
  const metadata = useModelMetadata(FOLDER_MODEL);
  const resource = metadata?.resource ?? null;
  const operationDocuments = useOperationDocuments();
  const resourceName = resource ? refineResourceName(resource) : "";
  const fields = refineFieldsFromPaths(["name"]);
  const createFolder = useCreate<RowRecord, HttpError, Record<string, unknown>>({
    resource: resourceName,
    dataProviderName: resource?.schemaName,
    meta: { fields },
    invalidates: ["list", "many"],
  });
  const updateFolder = useUpdate<RowRecord, HttpError, Record<string, unknown>>({
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

  return {
    busy,
    create: ({ drive, name, parent }) =>
      run(async () => {
        requireFolderResource(resource);
        await createFolder.mutateAsync({ values: { drive, name, parent } });
      }),
    rename: (id, name) =>
      run(async () => {
        requireFolderResource(resource);
        await updateFolder.mutateAsync({ id, values: { name } });
      }),
    remove: (id) =>
      run(async () => {
        requireFolderResource(resource);
        await deletePreview.mutate({ id, confirm: true });
        await invalidate({
          resource: refineResourceName(resource),
          dataProviderName: resource.schemaName,
          id,
          invalidates: ["list", "many", "detail"],
        });
      }),
  };
}

const FOLDER_MODEL = "storage.Folder";

type RowRecord = BaseRecord & Row;

function requireFolderResource(
  resource: NonNullable<ReturnType<typeof useModelMetadata>>["resource"] | null,
): asserts resource is NonNullable<ReturnType<typeof useModelMetadata>>["resource"] {
  if (!resource) {
    throw new Error(`Resource metadata for "${FOLDER_MODEL}" is not available.`);
  }
}
