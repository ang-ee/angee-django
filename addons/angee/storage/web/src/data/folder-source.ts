import { Filter, ResourceQuery, type ModelMetadata, type QueryFilter } from "@angee/metadata";
import { collectionQuery, type CollectionPageRequest, type CollectionSource } from "@angee/ui";
import { StorageFoldersPage } from "./documents";
import { storageFolderRow, type StorageTreeRow } from "./file-rows";

export const FOLDER_MODEL = "storage.Folder";
export const FOLDER_MODELS = [FOLDER_MODEL] as const;

/** Physical folders stay on their drive; each expanded branch owns a native server page. */
export function folderCollectionFilter(drive: string, request: CollectionPageRequest): QueryFilter {
  const branch = request.parentId ? { parent: { exact: request.parentId } }
    : Filter.from(request.filter).hasEntries() ? {} : { parent: { isNull: true } };
  return Filter.from({ drive: { exact: drive }, is_virtual: { exact: false } })
    .and(Filter.from(request.filter).and(branch));
}

/** Storage declares transport/count units; the shared collection owns every page and cache. */
export function folderSource(metadata: ModelMetadata, drive: string): CollectionSource<StorageTreeRow> {
  const query = ResourceQuery.from(metadata);
  return {
    query,
    rows: collectionQuery({
      document: StorageFoldersPage, models: FOLDER_MODELS, enabled: Boolean(drive),
      variables: (request: CollectionPageRequest) => ({
        where: query.toWhere(folderCollectionFilter(drive, request)),
        order: [query.toOrderBy(request.order ?? { name: "ASC" })],
        limit: request.pageSize, offset: (request.page - 1) * request.pageSize,
      }),
      select: (data) => ({ rows: data.folders.map(storageFolderRow), total: data.folders_aggregate.aggregate?.count ?? 0 }),
    }),
  };
}
