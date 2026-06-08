import { useMemo, useState, type ReactElement } from "react";

import {
  EmptyState,
  Explorer,
  LoadingPanel,
  PreviewPane,
  RowsListView,
  Select,
  TreeView,
  type PreviewFile,
} from "@angee/base";
import { useAuthoredQuery } from "@angee/sdk";

import {
  STORAGE_DRIVES_QUERY,
  STORAGE_FILES_QUERY,
  STORAGE_FOLDERS_QUERY,
  type OffsetPaginationVariables,
  type StorageDrivesData,
  type StorageFile,
  type StorageFilesData,
  type StorageFoldersData,
} from "../data/documents";
import {
  ALL_SCOPE,
  fileById,
  fileRows,
  folderTreeRows,
  type StorageTreeRow,
} from "../data/file-rows";
import { fileColumns } from "./file-columns";

// One safety-capped read each of drives/folders/files; the browser scopes the
// set client-side so the navigator, list, and preview share one fetch.
const STORAGE_LIST_LIMIT = 500;

/**
 * The file browser: an `Explorer` of a folder navigator, the scoped file list,
 * and a preview aside. Drives/folders/files load once; the drive switcher and
 * folder tree drive client-side scoping, and a row click previews the file.
 */
export function StoragePage(): ReactElement {
  const variables = useMemo<OffsetPaginationVariables>(
    () => ({ pagination: { offset: 0, limit: STORAGE_LIST_LIMIT } }),
    [],
  );
  const drivesQuery = useAuthoredQuery<StorageDrivesData, OffsetPaginationVariables>(
    STORAGE_DRIVES_QUERY,
    variables,
  );
  const foldersQuery = useAuthoredQuery<StorageFoldersData, OffsetPaginationVariables>(
    STORAGE_FOLDERS_QUERY,
    variables,
  );
  const filesQuery = useAuthoredQuery<StorageFilesData, OffsetPaginationVariables>(
    STORAGE_FILES_QUERY,
    variables,
  );

  const drives = drivesQuery.data?.drives.results ?? [];
  const folders = foldersQuery.data?.folders.results ?? [];
  const files = filesQuery.data?.files.results ?? [];

  const [pinnedDriveId, setPinnedDriveId] = useState<string | null>(null);
  const [scope, setScope] = useState<string>(ALL_SCOPE);
  const [selectedFileId, setSelectedFileId] = useState<string | null>(null);

  // Default to the first drive until the user picks one.
  const driveId = pinnedDriveId ?? drives[0]?.id ?? "";

  const driveOptions = useMemo(
    () => drives.map((drive) => ({ value: drive.id, label: drive.name || drive.slug })),
    [drives],
  );
  const treeRows = useMemo(
    () => folderTreeRows(folders, driveId),
    [folders, driveId],
  );
  const rows = useMemo(
    () => fileRows(files, { driveId, scope }),
    [files, driveId, scope],
  );
  const selectedFile = useMemo(
    () => fileById(files, selectedFileId),
    [files, selectedFileId],
  );

  if (drivesQuery.fetching && drives.length === 0) {
    return <LoadingPanel message="Loading storage" />;
  }
  if (drives.length === 0) {
    return (
      <div className="grid h-full place-content-center p-8">
        <EmptyState
          icon="drive"
          title={drivesQuery.error ? "Storage unavailable" : "No drives"}
          description={
            drivesQuery.error?.message ?? "No storage drives are available to you."
          }
        />
      </div>
    );
  }

  const navigator = (
    <div className="flex h-full flex-col gap-2 p-2">
      <Select
        value={driveId}
        options={driveOptions}
        placeholder="Select a drive"
        onValueChange={(value) => {
          setPinnedDriveId(value);
          setScope(ALL_SCOPE);
          setSelectedFileId(null);
        }}
      />
      <TreeView<StorageTreeRow>
        rows={treeRows}
        parent="parent"
        label="name"
        rowKey="id"
        icon="icon"
        selectedId={scope}
        onSelect={(row) => {
          setScope(row.id);
          setSelectedFileId(null);
        }}
        className="min-h-0 flex-1 overflow-auto"
      />
    </div>
  );

  return (
    <Explorer
      autoSave="storage.browser"
      navigator={navigator}
      aside={<FilePreview file={selectedFile} />}
    >
      <RowsListView
        rows={rows}
        columns={fileColumns}
        fetching={filesQuery.fetching}
        error={filesQuery.error}
        onRowClick={(row) => setSelectedFileId(row.id)}
        emptyMessage="No files here yet."
        pageSize={50}
      />
    </Explorer>
  );
}

function FilePreview({ file }: { file: StorageFile | null }): ReactElement {
  if (!file) {
    return (
      <div className="grid h-full place-content-center p-6">
        <EmptyState
          icon="file"
          title="Select a file"
          description="Choose a file from the list to preview it."
        />
      </div>
    );
  }
  const previewFile: PreviewFile = {
    url: file.url,
    name: file.filename,
    mime: file.mimeType?.mimeType ?? null,
    size: file.sizeBytes,
  };
  return (
    <div className="h-full overflow-auto p-3">
      <PreviewPane
        file={previewFile}
        fallback={
          <EmptyState
            icon="file"
            title={file.title || file.filename}
            description="No inline preview for this file type."
          />
        }
      />
    </div>
  );
}
