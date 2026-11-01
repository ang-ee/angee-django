import { type Row, } from "@angee/metadata";
import {
  useCreate, useUpdate, type BaseRecord, type HttpError, } from "@refinedev/core";
import {
  refineFieldsFromPaths, useReconcileMutationResponse, } from "@angee/refine";
import {
  refineResourceName, } from "@angee/metadata";
import { useBusyRun, useDeleteWithPreview } from "@angee/ui";
import {
  useModelMetadata,
  useResourceInvalidates,
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
 * backed by the storage manager factory server-side. Shared reconciliation
 * covers both the authored tree and native resource views.
 */
export function useFolderActions(): FolderActions {
  const metadata = useModelMetadata(FOLDER_MODEL);
  const resource = metadata?.resource ?? null;
  const reconcile = useReconcileMutationResponse();
  const folderInvalidations = useResourceInvalidates([FOLDER_MODEL]);
  const fileInvalidations = useResourceInvalidates([FILE_MODEL]);
  const affectedInvalidations = useResourceInvalidates([FOLDER_MODEL, FILE_MODEL],
    (_data: unknown, input: { id?: string | number }) => [{ model: FOLDER_MODEL, id: String(input.id) }]);
  const resourceName = refineResourceName(resource);
  const fields = refineFieldsFromPaths(["id", "name"]);
  const createFolder = useCreate<RowRecord, HttpError, Record<string, unknown>>({
    resource: resourceName,
    dataProviderName: resource?.schemaName,
    meta: { fields },
    invalidates: [],
    mutationOptions: { onSuccess: (result) => reconcile(result.data, undefined, {
      records: [{ model: FOLDER_MODEL, id: String(result.data.id), action: "create" }],
      invalidates: folderInvalidations,
    }) },
  });
  const updateFolder = useUpdate<RowRecord, HttpError, Record<string, unknown>>({
    resource: resourceName,
    dataProviderName: resource?.schemaName,
    meta: { fields },
    invalidates: [],
    mutationOptions: { onSuccess: (result, input) => reconcile(result.data, input, {
      records: [{ model: FOLDER_MODEL, id: String(input.id) }],
      invalidateModels: [FILE_MODEL], invalidates: affectedInvalidations,
    }) },
  });
  const deleteWithPreview = useDeleteWithPreview(resource);
  const { busy, run } = useBusyRun();

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
        await reconcile.batch(async (reconciliation) => {
          await deleteWithPreview.remove(id, { reconciliation });
          await reconcile(undefined, undefined, {
            invalidateModels: [FILE_MODEL], invalidates: fileInvalidations, reconciliation,
          });
        });
      }),
  };
}

const FOLDER_MODEL = "storage.Folder";
const FILE_MODEL = "storage.File";

type RowRecord = BaseRecord & Row;

function requireFolderResource(
  resource: NonNullable<ReturnType<typeof useModelMetadata>>["resource"] | null,
): asserts resource is NonNullable<ReturnType<typeof useModelMetadata>>["resource"] {
  if (!resource) {
    throw new Error(`Resource metadata for "${FOLDER_MODEL}" is not available.`);
  }
}
