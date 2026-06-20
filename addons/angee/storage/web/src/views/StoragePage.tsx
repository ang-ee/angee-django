import { useCallback, useMemo, useState, type ReactElement } from "react";
import { useNavigate, useParams } from "@tanstack/react-router";

import {
  EmptyState,
  Explorer,
  Glyph,
  LoadingPanel,
  PreviewPane,
  RelationPicker,
  recordPath,
  SelectionBarAction,
  TreeView,
  useConfirm,
  type FieldDescriptor,
  type PreviewFile,
} from "@angee/base";
import { useAuthoredQuery } from "@angee/sdk";

import {
  StorageBackends,
  StorageDrives,
  StorageFiles,
  StorageFolders,
  type StorageFile,
} from "../data/documents";
import {
  ALL_SCOPE,
  STORAGE_FILE_DND,
  TRASH_SCOPE,
  fileById,
  fileRows,
  folderTreeRows,
  type FileDragData,
  type StorageFileRow,
  type StorageTreeRow,
} from "../data/file-rows";
import { useFileActions } from "../data/use-file-actions";
import { useFolderActions } from "../data/use-folder-actions";
import { useStorageUpload } from "../data/use-upload";
import { FileBrowserContent } from "./FileBrowserContent";
import { FileDetail } from "./FileDetail";
import { NewFolderControl } from "./NewFolderControl";
import { SelectedFolderControl } from "./SelectedFolderControl";
import { useStorageT } from "../i18n";

// One safety-capped read each of drives/folders/files; the browser scopes the
// set client-side so the navigator, list, and preview share one fetch.
const STORAGE_LIST_LIMIT = 500;

/**
 * The file browser: an `Explorer` of a folder navigator, the scoped file list,
 * and a preview aside. Drives/folders/files load once; the drive switcher and
 * folder tree drive client-side scoping, and a row click previews the file.
 */
export function StoragePage(): ReactElement {
  const t = useStorageT();
  const variables = useMemo(
    () => ({ pagination: { offset: 0, limit: STORAGE_LIST_LIMIT } }),
    [],
  );
  const drivesQuery = useAuthoredQuery(StorageDrives, variables);
  const foldersQuery = useAuthoredQuery(StorageFolders, variables);
  const filesQuery = useAuthoredQuery(StorageFiles, variables);
  // Admin-only catalogue for the inline drive-create form's backend picker.
  const backendsQuery = useAuthoredQuery(StorageBackends, variables);

  const drives = drivesQuery.data?.drives.results ?? [];
  const folders = foldersQuery.data?.folders.results ?? [];
  const files = filesQuery.data?.files.results ?? [];
  const backends = backendsQuery.data?.backends.results ?? [];

  // The open file is route state: `/storage/$id` swaps the content to the detail
  // form and the aside to that file's larger preview; `/storage` is the list.
  const navigate = useNavigate();
  const params = useParams({ strict: false });
  const openFileId =
    "id" in params && typeof params.id === "string" ? params.id : null;
  const closeDetail = useCallback(() => {
    void navigate({ to: "/storage" });
  }, [navigate]);

  const [pinnedDriveId, setPinnedDriveId] = useState<string | null>(null);
  const [scope, setScope] = useState<string>(ALL_SCOPE);

  // Default to the first drive until the user picks one.
  const driveId = pinnedDriveId ?? drives[0]?.id ?? "";

  const driveOptions = useMemo(
    () => drives.map((drive) => ({ value: drive.id, label: drive.name || drive.slug })),
    [drives],
  );
  // The inline drive-create form. `name` is the record title (prefilled with the
  // typed query); `backend` is the required FK, picked from the catalogue above.
  // This stays a passed `fields` (not a `forms:` registration) because its
  // `backend` options are fetched at runtime — a static module-scope form override
  // cannot carry them (cf. the static `Vault` form in the knowledge manifest).
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
  const openFile = useMemo(
    () => fileById(files, openFileId),
    [files, openFileId],
  );
  const rowHref = useCallback(
    (row: StorageFileRow) => recordPath("/storage", row.id),
    [],
  );
  const uploads = useStorageUpload({ onUploaded: () => filesQuery.refetch() });
  const fileActions = useFileActions({ onChanged: () => filesQuery.refetch() });
  const folderActions = useFolderActions({
    // A folder write can move files (delete falls them back to the root), so
    // refetch both trees.
    onChanged: () => {
      void foldersQuery.refetch();
      void filesQuery.refetch();
    },
  });
  const confirm = useConfirm();
  // Dropping a file on a navigator node moves it: the Trash node trashes, All
  // files moves to the drive root, any folder node moves into that folder.
  const handleFileDrop = useCallback(
    (nodeId: string, file: FileDragData) => {
      if (nodeId === TRASH_SCOPE) void fileActions.trash(file.id);
      else if (nodeId === ALL_SCOPE) void fileActions.move(file.id, null);
      else void fileActions.move(file.id, nodeId);
    },
    [fileActions],
  );
  // New folders land under the active folder scope (or the drive root).
  const handleNewFolder = useCallback(
    (name: string) => {
      if (!driveId) return;
      const parent =
        effectiveScope === ALL_SCOPE || effectiveScope === TRASH_SCOPE
          ? null
          : effectiveScope;
      void folderActions.create({ drive: driveId, name, parent });
    },
    [driveId, effectiveScope, folderActions],
  );
  // The active folder scope (a real folder, not the All/Trash pseudo-nodes); its
  // navigator footer offers rename + delete.
  const selectedFolder =
    effectiveScope !== ALL_SCOPE && effectiveScope !== TRASH_SCOPE
      ? treeRows.find((row) => row.id === effectiveScope)
      : undefined;
  const handleRenameFolder = (name: string): void => {
    void folderActions.rename(effectiveScope, name);
  };
  const handleDeleteFolder = async (): Promise<void> => {
    if (!selectedFolder) return;
    const ok = await confirm({
      title: t("storage.folder.deleteTitle", { name: selectedFolder.name }),
      body: t("storage.folder.deleteBody"),
      confirm: t("storage.folder.deleteConfirm"),
      danger: true,
    });
    if (!ok) return;
    void folderActions.remove(effectiveScope).then(() => setScope(ALL_SCOPE));
  };
  // The selection bar's bulk verbs: Restore in the Trash scope, else Trash.
  const renderBulkActions = (ids: ReadonlySet<string>, clear: () => void) =>
    effectiveScope === TRASH_SCOPE ? (
      <SelectionBarAction
        surface="brand"
        pending={fileActions.busy}
        onClick={() => void fileActions.restoreMany(ids).then(clear)}
      >
        <Glyph name="restore" />
        {t("storage.bulk.restore")}
      </SelectionBarAction>
    ) : (
      <SelectionBarAction
        surface="brand"
        pending={fileActions.busy}
        onClick={() => void fileActions.trashMany(ids).then(clear)}
      >
        <Glyph name="trash" />
        {t("storage.bulk.trash")}
      </SelectionBarAction>
    );
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
    return <LoadingPanel message={t("storage.loading")} />;
  }
  if (drives.length === 0) {
    return (
      <EmptyState
        fill
        icon="drive"
        title={
          drivesQuery.error
            ? t("storage.drives.unavailableTitle")
            : t("storage.drives.emptyTitle")
        }
        description={
          drivesQuery.error?.message ?? t("storage.drives.emptyDescription")
        }
      />
    );
  }

  const navigator = (
    <div className="flex h-full flex-col gap-2 p-2">
      <RelationPicker
        aria-label={t("storage.drive.label")}
        value={driveId}
        options={driveOptions}
        placeholder={t("storage.drive.placeholder")}
        searchPlaceholder={t("storage.drive.searchPlaceholder")}
        onChange={(value) => {
          setPinnedDriveId(value);
          setScope(ALL_SCOPE);
          closeDetail();
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
          closeDetail();
        }}
        dropAccept={STORAGE_FILE_DND}
        onNodeDrop={(nodeId, payload) =>
          handleFileDrop(nodeId, payload.data as FileDragData)
        }
        className="min-h-0 flex-1 overflow-auto"
      />
      {selectedFolder ? (
        <SelectedFolderControl
          key={selectedFolder.id}
          name={selectedFolder.name}
          busy={folderActions.busy}
          onRename={handleRenameFolder}
          onDelete={handleDeleteFolder}
        />
      ) : null}
      <NewFolderControl busy={folderActions.busy} onCreate={handleNewFolder} />
    </div>
  );

  return (
    <Explorer
      autoSave="storage.browser"
      navigator={navigator}
      aside={<FilePreview file={openFile} />}
    >
      {openFileId ? (
        openFile ? (
          <FileDetail
            file={openFile}
            onClose={closeDetail}
            onChanged={() => filesQuery.refetch()}
          />
        ) : filesQuery.fetching ? (
          <LoadingPanel message={t("storage.loadingFile")} />
        ) : (
          <EmptyState
            fill
            icon="file"
            title={t("storage.file.notFoundTitle")}
            description={t("storage.file.notFoundDescription")}
          />
        )
      ) : (
        <FileBrowserContent
          rows={rows}
          fetching={filesQuery.fetching}
          error={filesQuery.error}
          rowHref={rowHref}
          bulkActions={renderBulkActions}
          uploads={uploads}
          uploadTarget={uploadTarget}
          canUpload={canUpload}
        />
      )}
    </Explorer>
  );
}

function FilePreview({ file }: { file: StorageFile | null }): ReactElement {
  const t = useStorageT();
  if (!file) {
    return (
      <EmptyState
        fill
        icon="file"
        title={t("storage.preview.emptyTitle")}
        description={t("storage.preview.emptyDescription")}
      />
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
            description={t("storage.preview.unsupported")}
          />
        }
      />
    </div>
  );
}
