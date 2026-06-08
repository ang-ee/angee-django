import { useMemo, useState, type ReactElement } from "react";

import {
  EmptyState,
  Explorer,
  LoadingPanel,
  PreviewPane,
  RelationPicker,
  TreeView,
  type FieldDescriptor,
  type PreviewFile,
} from "@angee/base";
import { useAuthoredQuery } from "@angee/sdk";

import {
  STORAGE_BACKENDS_QUERY,
  STORAGE_DRIVES_QUERY,
  STORAGE_FILES_QUERY,
  STORAGE_FOLDERS_QUERY,
  type OffsetPaginationVariables,
  type StorageBackendsData,
  type StorageDrivesData,
  type StorageFile,
  type StorageFilesData,
  type StorageFoldersData,
} from "../data/documents";
import {
  ALL_SCOPE,
  TRASH_SCOPE,
  fileById,
  fileRows,
  folderTreeRows,
  type StorageTreeRow,
} from "../data/file-rows";
import { useStorageUpload } from "../data/use-upload";
import { FileBrowserContent } from "./FileBrowserContent";

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
  // Admin-only catalogue for the inline drive-create form's backend picker.
  const backendsQuery = useAuthoredQuery<StorageBackendsData, OffsetPaginationVariables>(
    STORAGE_BACKENDS_QUERY,
    variables,
  );

  const drives = drivesQuery.data?.drives.results ?? [];
  const folders = foldersQuery.data?.folders.results ?? [];
  const files = filesQuery.data?.files.results ?? [];
  const backends = backendsQuery.data?.backends.results ?? [];

  const [pinnedDriveId, setPinnedDriveId] = useState<string | null>(null);
  const [scope, setScope] = useState<string>(ALL_SCOPE);
  const [selectedFileId, setSelectedFileId] = useState<string | null>(null);

  // Default to the first drive until the user picks one.
  const driveId = pinnedDriveId ?? drives[0]?.id ?? "";

  const driveOptions = useMemo(
    () => drives.map((drive) => ({ value: drive.id, label: drive.name || drive.slug })),
    [drives],
  );
  // The inline drive-create form. `name` is the record title (prefilled with the
  // typed query); `backend` is the required FK, picked from the catalogue above.
  const driveCreateFields = useMemo<readonly FieldDescriptor[]>(
    () => [
      { name: "name", label: "Name" },
      { name: "slug", label: "Slug", placeholder: "assets" },
      {
        // A bare-ID FK (DriveType.backend is `ID`, not an object), so this is a
        // plain `select` — `many2one` would make the form select `backend.id`,
        // which the scalar field has no subfield for.
        name: "backend",
        label: "Backend",
        widget: "select",
        options: backends.map((backend) => ({
          value: backend.id,
          label: backend.label || backend.slug,
        })),
      },
      { name: "prefix", label: "Prefix", placeholder: "optional key prefix" },
      { name: "description", label: "Description", widget: "textarea" },
    ],
    [backends],
  );
  const treeRows = useMemo(
    () => folderTreeRows(folders, driveId),
    [folders, driveId],
  );
  // Clamp the scope to the active drive: if a folder scope no longer names a
  // node in this drive's tree (e.g. the default drive shifted out from under an
  // unpinned session), fall back to All files instead of an empty list with no
  // highlighted node.
  const effectiveScope =
    scope === ALL_SCOPE ||
    scope === TRASH_SCOPE ||
    treeRows.some((row) => row.id === scope)
      ? scope
      : ALL_SCOPE;
  const rows = useMemo(
    () => fileRows(files, { driveId, scope: effectiveScope }),
    [files, driveId, effectiveScope],
  );
  const selectedFile = useMemo(
    () => fileById(files, selectedFileId),
    [files, selectedFileId],
  );
  const uploads = useStorageUpload({ onUploaded: () => filesQuery.refetch() });
  // Uploads land in the active drive, into the current folder (or its root); the
  // Trash scope is not an upload target.
  const canUpload = driveId !== "" && effectiveScope !== TRASH_SCOPE;
  const uploadTarget = useMemo(
    () => ({
      driveId,
      folderId:
        effectiveScope === ALL_SCOPE || effectiveScope === TRASH_SCOPE
          ? null
          : effectiveScope,
    }),
    [driveId, effectiveScope],
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
      <RelationPicker
        aria-label="Drive"
        value={driveId}
        options={driveOptions}
        placeholder="Select a drive"
        searchPlaceholder="Search drives…"
        onChange={(value) => {
          setPinnedDriveId(value);
          setScope(ALL_SCOPE);
          setSelectedFileId(null);
        }}
        create={{ model: "Drive", fields: driveCreateFields }}
        onCreated={() => drivesQuery.refetch()}
      />
      <TreeView<StorageTreeRow>
        rows={treeRows}
        parent="parent"
        label="name"
        rowKey="id"
        icon="icon"
        selectedId={effectiveScope}
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
      <FileBrowserContent
        rows={rows}
        fetching={filesQuery.fetching}
        error={filesQuery.error}
        onRowClick={(row) => setSelectedFileId(row.id)}
        uploads={uploads}
        uploadTarget={uploadTarget}
        canUpload={canUpload}
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
