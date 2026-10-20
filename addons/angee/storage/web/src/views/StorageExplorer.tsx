import { useAuthoredQuery, useAuthoredQueryBatch, useInvalidateAuthoredModels } from "@angee/refine";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactElement, type ReactNode } from "react";
import {
  EmptyState, InlineTextAction, LoadingPanel, PaneToolbarMenu, ScopedExplorerPane, TreeView,
  useConfirm, useLatestRef, type FieldDescriptor, type ScopedExplorerController,
} from "@angee/ui";

import {
  STORAGE_CATALOGUE_LIMIT, StorageBackends, StorageDrives, StorageFolderChildren, StorageFolderRoots,
  type StorageDrive, type StorageFile,
} from "../data/documents";
import { ALL_SCOPE, STORAGE_FILE_DND, TRASH_SCOPE, folderTreeRows, type FileDragData, type StorageTreeRow } from "../data/file-rows";
import type { FileActions } from "../data/use-file-actions";
import type { StorageUpload } from "../data/use-upload";
import { useFolderActions } from "../data/use-folder-actions";
import { useStorageT } from "../i18n";

const FOLDER_MODEL = "storage.Folder";
const FOLDER_MODELS = [FOLDER_MODEL] as const;
// Each expanded parent loads independently; the file list has its own pagination.
const FOLDER_PAGE_LIMIT = 5000;

// Stable field projections for the drive tree roots: module-scope so the
// explorer's option list keeps a stable identity (the navigator is published
// into the shell primary pane and must not churn on every render).
const driveRootId = (drive: StorageDrive): string => drive.id;
const driveRootLabel = (drive: StorageDrive): string => drive.name || drive.slug;

export type StorageExplorerController = ScopedExplorerController<
  StorageDrive,
  StorageTreeRow
>;

interface ExpandedFolderRequest {
  key: string;
  drive: string;
  parent: string;
}

function expandedFolderKey(drive: string, parent: string): string {
  return `${drive}\0${parent}`;
}

/** Storage's lazy folder tree and folder verbs, composed into the shared explorer. */
export function StorageExplorer({ openFile, openFileId, folderScope, selectScope, openFileRoute, fileActions, uploads, children }: {
  openFile: StorageFile | null;
  openFileId: string | null;
  folderScope: string;
  selectScope: (scope: string | null) => void;
  openFileRoute: (id: string) => void;
  fileActions: FileActions;
  /** The upload owner; present on the files page, where "New" offers Upload. */
  uploads?: StorageUpload;
  children: (controller: StorageExplorerController) => ReactNode;
}): ReactElement {
  const t = useStorageT();
  const catalogueVariables = useMemo(
    () => ({ offset: 0, limit: STORAGE_CATALOGUE_LIMIT }),
    [],
  );
  const drivesQuery = useAuthoredQuery(StorageDrives, catalogueVariables);
  // Admin-only catalogue for the inline drive-create form's backend picker.
  const backendsQuery = useAuthoredQuery(StorageBackends, catalogueVariables);

  const drives = drivesQuery.data?.drives ?? [];
  const backends = backendsQuery.data?.backends ?? [];

  const [activeDriveId, setActiveDriveId] = useState<string | null>(null);
  const folderDriveId =
    openFile?.drive ?? activeDriveId ?? drives[0]?.id ?? "";
  useEffect(() => {
    if (openFile?.drive) setActiveDriveId(openFile.drive);
  }, [openFile?.drive]);
  const rootsQuery = useAuthoredQuery(
    StorageFolderRoots,
    { drive: folderDriveId, limit: FOLDER_PAGE_LIMIT },
    { enabled: folderDriveId !== "", models: [FOLDER_MODEL] },
  );
  // The only local tree fact is which folders the user has asked to expand.
  // Keep requests from prior drives warm for instant re-expansion; this list
  // grows only with the user's expansions, while inactive-drive entries do not
  // enter the current batch.
  const [expandedFolders, setExpandedFolders] = useState<
    readonly ExpandedFolderRequest[]
  >([]);
  const activeExpandedFolders = useMemo(
    () => expandedFolders.filter((request) => request.drive === folderDriveId),
    [expandedFolders, folderDriveId],
  );
  const folderChildScopes = useMemo(
    () =>
      activeExpandedFolders.map((request) => ({
        key: request.key,
        document: StorageFolderChildren,
        variables: {
          drive: request.drive,
          parent: request.parent,
          limit: FOLDER_PAGE_LIMIT,
        },
        models: FOLDER_MODELS,
      })),
    [activeExpandedFolders],
  );
  const folderChildQueries = useAuthoredQueryBatch(folderChildScopes, {
    enabled: folderDriveId !== "",
  });
  const folderChildQueriesRef = useLatestRef(folderChildQueries);
  const handleExpandFolder = useCallback(
    (nodeId: string) => {
      if (!folderDriveId) return;
      const key = expandedFolderKey(folderDriveId, nodeId);
      const current = folderChildQueriesRef.current.get(key);
      if (current?.error) {
        current.refetch();
        return;
      }
      setExpandedFolders((previous) =>
        previous.some((request) => request.key === key)
          ? previous
          : [...previous, { key, drive: folderDriveId, parent: nodeId }],
      );
    },
    [folderChildQueriesRef, folderDriveId],
  );
  const invalidateAuthoredModels = useInvalidateAuthoredModels();
  // Folder CRUD invalidates every authored Folder read through its model key;
  // server-originated writes arrive through storage's `folderChanged` schema
  // subscription and the same authored-query metadata.
  const handleFolderMutation = useCallback(() => {
    invalidateAuthoredModels(FOLDER_MODELS);
  }, [invalidateAuthoredModels]);
  const rootFolders = rootsQuery.data?.folders;
  const folderTreeState = useMemo(
    () => {
      const folders = [...(rootFolders ?? [])];
      const resolvedParents = new Set<string>();
      const loadingParents = new Set<string>();
      for (const request of activeExpandedFolders) {
        const query = folderChildQueries.get(request.key);
        if (query?.isFetching) loadingParents.add(request.parent);
        if (query?.data === undefined) continue;
        resolvedParents.add(request.parent);
        folders.push(...query.data.folders);
      }
      return { folders, resolvedParents, loadingParents };
    },
    [activeExpandedFolders, folderChildQueries, rootFolders],
  );
  const getTreeRows = useCallback(
    (rootId: string) =>
      folderTreeRows(
        folderTreeState.folders,
        rootId,
        folderTreeState.resolvedParents,
        openFile,
        folderTreeState.loadingParents,
      ),
    [folderTreeState, openFile],
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
  const folderActions = useFolderActions({
    onChanged: handleFolderMutation,
  });
  const confirm = useConfirm();
  const { refetch: refetchDrives } = drivesQuery;
  // The action hooks return a fresh object each render; the navigator is
  // published into the shell primary pane, so its callbacks read the live
  // actions through a ref and stay referentially stable across renders.
  const fileActionsRef = useLatestRef(fileActions);
  const folderActionsRef = useLatestRef(folderActions);
  // Dropping a file on a navigator node moves it: the Trash node trashes, All
  // files moves to the drive root, any folder node moves into that folder.
  const handleFileDrop = useCallback(
    (nodeId: string, file: FileDragData) => {
      const actions = fileActionsRef.current;
      if (nodeId === TRASH_SCOPE) void actions.trash(file.id);
      else if (nodeId === ALL_SCOPE) void actions.move(file.id, null);
      else void actions.move(file.id, nodeId);
    },
    [],
  );
  const driveRootPicker = useMemo(
    () => ({
      "aria-label": t("drive.label"),
      placeholder: t("drive.placeholder"),
      searchPlaceholder: t("drive.searchPlaceholder"),
      create: { resource: "storage.Drive", fields: driveCreateFields },
      onCreated: (id: string) => {
        setActiveDriveId(id);
        void refetchDrives();
      },
    }),
    [driveCreateFields, refetchDrives, t],
  );
  // The folder verbs happen where the user looks: New folder is typed as a row in
  // the tree under the selected folder; Rename edits the selected folder's own row.
  const [folderEdit, setFolderEdit] = useState<"create" | "rename" | null>(null);
  const uploadInputRef = useRef<HTMLInputElement>(null);
  // The upload owner is read live so the published navigator stays stable across renders.
  const uploadsRef = useLatestRef(uploads);
  const canUpload = uploads !== undefined;
  const renderTree = useCallback(
    (controller: StorageExplorerController) => {
      const effectiveScope = controller.selectedId ?? ALL_SCOPE;
      const selectedFolder =
        effectiveScope !== ALL_SCOPE && effectiveScope !== TRASH_SCOPE
          ? controller.selectedRow
          : undefined;
      const submit = (name: string): void => {
        if (folderEdit === "rename") {
          void folderActionsRef.current.rename(effectiveScope, name);
        } else if (controller.rootId) {
          void folderActionsRef.current.create({
            drive: controller.rootId,
            name,
            parent: selectedFolder ? effectiveScope : null,
          });
        }
        setFolderEdit(null);
      };
      const field = (value?: string): ReactNode => (
        <InlineTextAction
          key={`${folderEdit}:${selectedFolder?.id ?? ""}`}
          open
          onOpenChange={(next) => { if (!next) setFolderEdit(null); }}
          value={value}
          busy={folderActions.busy}
          onSubmit={submit}
          inputLabel={folderEdit === "rename" ? t("folder.nameLabel") : t("newFolder.nameLabel")}
          placeholder={t("newFolder.placeholder")}
          submitLabel={folderEdit === "rename" ? t("folder.save") : t("newFolder.create")}
          formClassName="w-full items-center gap-1"
          inputClassName="min-w-0 flex-1"
        />
      );
      return (
        <>
          <TreeView<StorageTreeRow>
            rows={controller.treeRows}
            parent="parent"
            label="name"
            rowKey="id"
            icon="icon"
            hasChildren="hasChildren"
            loading="loading"
            onExpand={handleExpandFolder}
            selectedId={openFileId ?? (controller.selectedId ?? ALL_SCOPE)}
            onSelect={(row) => {
              if (row.kind === "file") {
                openFileRoute(row.id);
                return;
              }
              // Writes the scope to the URL (via onSelectedIdChange) and lands on the
              // list route, closing any open preview.
              controller.setSelectedId(row.id);
            }}
            renderRow={folderEdit === "rename" && selectedFolder
              ? (row) => (row.id === selectedFolder.id ? field(selectedFolder.name) : row.name)
              : undefined}
            insertion={folderEdit === "create"
              ? { parentId: selectedFolder ? effectiveScope : null, node: field() }
              : null}
            dropAccept={STORAGE_FILE_DND}
            canDropOnNode={(_nodeId, row) => row.kind !== "file"}
            onNodeDrop={(nodeId, payload) =>
              handleFileDrop(nodeId, payload.data as FileDragData)
            }
            className="min-h-0 flex-1 overflow-auto"
          />
          {canUpload ? (
            <input
              ref={uploadInputRef}
              type="file"
              multiple
              className="hidden"
              aria-label={t("upload.chooseFiles")}
              onChange={(event) => {
                const files = event.target.files;
                if (files && files.length > 0 && controller.rootId) {
                  uploadsRef.current?.upload(Array.from(files), {
                    driveId: controller.rootId,
                    folderId: selectedFolder ? effectiveScope : null,
                  });
                }
                event.target.value = "";
              }}
            />
          ) : null}
        </>
      );
    },
    [canUpload, folderActions.busy, folderActionsRef, folderEdit, handleExpandFolder, handleFileDrop, openFileId, openFileRoute, t, uploadsRef],
  );
  const explorerToolbar = useCallback(
    (controller: StorageExplorerController) => {
      const effectiveScope = controller.selectedId ?? ALL_SCOPE;
      const selectedFolder =
        effectiveScope !== ALL_SCOPE && effectiveScope !== TRASH_SCOPE
          ? controller.selectedRow
          : undefined;
      const deleteFolder = async (): Promise<void> => {
        if (!selectedFolder) return;
        const ok = await confirm({
          title: t("folder.deleteTitle", { name: selectedFolder.name }),
          body: t("folder.deleteBody"),
          confirm: t("folder.deleteConfirm"),
          danger: true,
        });
        if (!ok) return;
        void folderActionsRef.current
          .remove(effectiveScope)
          .then(() => controller.setSelectedId(ALL_SCOPE));
      };
      const busy = folderActions.busy || !controller.rootId;
      return {
        actions: [{
          icon: "plus", label: t("new.menu"), text: true, disabled: busy,
          items: [
            { icon: "folder", label: t("new.folder"), onClick: () => setFolderEdit("create") },
            ...(canUpload ? [{ icon: "upload", label: t("new.upload"), onClick: () => uploadInputRef.current?.click() }] : []),
          ],
        }],
        overflow: selectedFolder ? (
          <PaneToolbarMenu items={[
            { icon: "pencil", label: t("folder.rename"), disabled: folderActions.busy, onClick: () => setFolderEdit("rename") },
            { icon: "trash", label: t("folder.delete"), tone: "danger", disabled: folderActions.busy, onClick: () => void deleteFolder() },
          ]} />
        ) : undefined,
      };
    },
    [canUpload, confirm, folderActions.busy, folderActionsRef, t],
  );
  const handleRootChange = useCallback(
    (rootId: string) => {
      setActiveDriveId(rootId);
      // A folder from the old drive is invalid here: reset the scope to All
      // files (dropping the `folder` param) and close any open preview.
      selectScope(ALL_SCOPE);
    },
    [selectScope],
  );

  return (
    <ScopedExplorerPane<StorageDrive, StorageTreeRow>
      roots={drives}
      getRootId={driveRootId}
      getRootLabel={driveRootLabel}
      getTreeRows={getTreeRows}
      selectedId={folderScope}
      onSelectedIdChange={selectScope}
      defaultSelectedId={ALL_SCOPE}
      selectedRootId={openFile?.drive ?? activeDriveId}
      isSelectedIdValid={(id, rows) =>
        id === ALL_SCOPE || id === TRASH_SCOPE || rows.some((row) => row.id === id)
      }
      navigatorLabel={t("nav.label")}
      rootPicker={driveRootPicker}
      onRootChange={handleRootChange}
      renderTree={renderTree}
      toolbar={explorerToolbar}
      renderContentWithoutRoots={openFileId !== null}
      loading={drivesQuery.isFetching && drives.length === 0}
      loadingContent={<LoadingPanel message={t("loading")} />}
      emptyContent={
        <EmptyState
          fill
          icon="drive"
          title={
            drivesQuery.error
              ? t("drives.unavailableTitle")
              : t("drives.emptyTitle")
          }
          description={
            drivesQuery.error?.message ?? t("drives.emptyDescription")
          }
        />
      }
    >
      {children}
    </ScopedExplorerPane>
  );
}
