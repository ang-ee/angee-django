import { useModelMetadata } from "@angee/metadata";
import { useAuthoredQuery, useInvalidateAuthoredModels } from "@angee/refine";
import { useCallback, useMemo, useRef, useState, type ReactElement, type ReactNode } from "react";
import {
  CollectionTreeView, EmptyState, ErrorBanner, InlineTextAction, LoadingPanel, PaneToolbar, PaneToolbarMenu,
  ResourceViewProvider, ScopedExplorerPane, TreeView, useConfirm, useFilePicker, useLatestRef,
  relationFieldInfoForResource, useRelationOptions, useScopedTreeExplorer,
  type FieldDescriptor, type ScopedExplorerController,
} from "@angee/ui";
import { StorageFolderById, type StorageDrive, type StorageFile } from "../data/documents";
import { ALL_SCOPE, STORAGE_FILE_DND, TRASH_SCOPE, fileDragData, folderTreeRows,
  type FileDragData, type StorageTreeRow } from "../data/file-rows";
import { FOLDER_MODEL, FOLDER_MODELS, folderSource } from "../data/folder-source";
import type { FileActions } from "../data/use-file-actions";
import type { StorageUpload, UploadTarget } from "../data/use-upload";
import { useFolderActions } from "../data/use-folder-actions";
import { useDrivePicker } from "../data/use-drive-picker";
import { useStorageT } from "../i18n";

const driveRootId = (drive: StorageDrive): string => drive.id;
type ScopedStorageController = ScopedExplorerController<StorageDrive, StorageTreeRow>;
export interface StorageExplorerController extends ScopedStorageController {
  /** A readable resolved scope is required before any upload surface can dispatch. */
  uploadTarget: UploadTarget | null;
}

function storageController(controller: ScopedStorageController): StorageExplorerController {
  const scope = controller.selectedId ?? ALL_SCOPE;
  const folder = controller.selectedRow?.kind === "folder" ? controller.selectedRow : null;
  const uploadTarget = !controller.rootId || scope === TRASH_SCOPE ? null
    : scope === ALL_SCOPE ? { driveId: controller.rootId, folderId: null }
      : folder ? { driveId: controller.rootId, folderId: folder.id } : null;
  return { ...controller, uploadTarget };
}

/** Storage declares scope and verbs; shared relation/collection owners page every readable row. */
export function StorageExplorer({ openFile: receivedOpenFile, openFileId, folderScope, selectScope, openFileRoute, fileActions, uploads, children }: {
  openFile: StorageFile | null;
  openFileId: string | null;
  folderScope: string;
  selectScope: (scope: string | null) => void;
  openFileRoute: (id: string) => void;
  fileActions: FileActions;
  uploads?: StorageUpload;
  children: (controller: StorageExplorerController) => ReactNode;
}): ReactElement {
  const t = useStorageT();
  const openFile = receivedOpenFile?.id === openFileId ? receivedOpenFile : null;
  const driveRootLabel = useCallback((drive: StorageDrive): string => drive.name || t("drive.unnamed"), [t]);
  const requestedFolder = folderScope !== ALL_SCOPE && folderScope !== TRASH_SCOPE ? folderScope : openFile?.folder ?? "";
  const folderRead = useAuthoredQuery(StorageFolderById, { id: requestedFolder }, {
    enabled: Boolean(requestedFolder), models: FOLDER_MODELS,
  });
  const folder = folderRead.data?.folders_by_pk?.id === requestedFolder ? folderRead.data.folders_by_pk : null;
  const driveMetadata = useModelMetadata("storage.Drive");
  const driveRelation = useMemo(() => relationFieldInfoForResource("storage.Drive", driveMetadata ?? null), [driveMetadata]);
  const initialDrives = useRelationOptions(driveRelation, { enabled: !openFileId && !requestedFolder, pageSize: 1, sort: true });
  const invalidateAuthoredModels = useInvalidateAuthoredModels();
  const handleFolderMutation = useCallback(() => {
    invalidateAuthoredModels(FOLDER_MODELS);
  }, [invalidateAuthoredModels]);
  // Scope/detail reads are independent of sibling pages. They retain the requested
  // folder for actions and deep links without copying the collection's row cache.
  const getTreeRows = useCallback((rootId: string) => folderTreeRows(
    folder && !folder.is_virtual ? [folder] : [], rootId, openFile,
  ), [folder, openFile]);
  const isSelectedIdValid = useCallback((id: string, rows: readonly StorageTreeRow[]) =>
    id === ALL_SCOPE || id === TRASH_SCOPE || rows.some((row) => row.id === id)
      || id === requestedFolder,
  [requestedFolder]);
  const controller = useScopedTreeExplorer({
    roots: initialDrives.options, getRootId: (option) => option.value, getTreeRows,
    selectedRootId: openFileId ? openFile?.drive ?? null : requestedFolder ? folder?.drive ?? null : undefined,
    selectedId: folderScope, onSelectedIdChange: selectScope, defaultSelectedId: ALL_SCOPE, isSelectedIdValid,
  });
  // Inline folder drafts belong to the actual scope, including resolved links.
  const [folderEdit, setFolderEdit] = useState<"create" | "rename" | null>(null);
  const [editingRootId, setEditingRootId] = useState(controller.rootId);
  if (editingRootId !== controller.rootId) {
    setEditingRootId(controller.rootId);
    setFolderEdit(null);
  }
  const { picker: driveOptions, roots: drives } = useDrivePicker(controller.rootId, Boolean(controller.rootId));
  const driveCreateFields = useMemo<readonly FieldDescriptor[]>(() => [
    { name: "name", label: t("drive.create.name") },
    { name: "slug", label: t("drive.create.slug"), placeholder: t("drive.create.slugPlaceholder") },
    { name: "backend", label: t("drive.create.backend"), widget: "many2one" },
    { name: "prefix", label: t("drive.create.prefix"), placeholder: t("drive.create.prefixPlaceholder") },
    { name: "description", label: t("drive.create.description"), widget: "textarea" },
  ], [t]);
  const folderActions = useFolderActions({
    onChanged: handleFolderMutation,
  });
  const confirm = useConfirm();
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
      onOpenChange: driveOptions.onOpenChange,
      onSearchChange: driveOptions.onSearchChange,
      searchState: driveOptions.searchState,
      create: { resource: "storage.Drive", fields: driveCreateFields },
    }),
    [driveCreateFields, driveOptions.onOpenChange, driveOptions.onSearchChange, driveOptions.searchState, t],
  );
  // The upload owner and its destination are read live so the published navigator
  // stays stable across renders; the tree render keeps the destination current.
  const uploadsRef = useLatestRef(uploads);
  const uploadTargetRef = useRef<UploadTarget | null>(null);
  const canUpload = uploads !== undefined;
  const picker = useFilePicker({
    label: t("upload.chooseFiles"),
    disabled: !canUpload,
    onFiles: (files) => {
      const target = uploadTargetRef.current;
      if (target) uploadsRef.current?.upload(files, target);
    },
  });
  const renderTree = useCallback(
    (controller: StorageExplorerController) => {
      const effectiveScope = controller.selectedId ?? ALL_SCOPE;
      const selectedFolder =
        effectiveScope !== ALL_SCOPE && effectiveScope !== TRASH_SCOPE
          ? controller.selectedRow
          : undefined;
      uploadTargetRef.current = controller.uploadTarget;
      const submit = async (name: string): Promise<void> => {
        if (folderEdit === "rename") {
          await folderActionsRef.current.rename(effectiveScope, name);
        } else if (controller.rootId) {
          await folderActionsRef.current.create({
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
          {folderRead.error ? <ErrorBanner description={folderRead.error.message} /> : null}
          <TreeView<StorageTreeRow>
            rows={controller.treeRows.filter((row) => row.kind === "scope" || row.kind === "file" && row.parent === ALL_SCOPE)}
            parent="parent" label="name" rowKey="id" icon="icon"
            selectedId={openFileId ?? controller.selectedId ?? ALL_SCOPE}
            onSelect={(row) => row.kind === "file" ? openFileRoute(row.id) : controller.setSelectedId(row.id)}
            dropAccept={STORAGE_FILE_DND} canDropOnNode={(_id, row) => row.kind !== "file"}
            onNodeDrop={(id, payload) => { const file = fileDragData(payload.data); if (file) handleFileDrop(id, file); }}
          />
          <ResourceViewProvider key={controller.rootId} scope="local" resource={FOLDER_MODEL}
            initialState={{ pageSize: 50, sort: { field: "name", dir: "asc" } }}>
            <StorageFolders drive={controller.rootId} selectedId={openFileId ?? controller.selectedId}
              revealRows={controller.treeRows.filter((row) => row.kind === "folder" || row.kind === "file" && row.parent !== ALL_SCOPE)}
              onSelect={(row) => row.kind === "file" ? openFileRoute(row.id) : controller.setSelectedId(row.id)}
              onFileDrop={handleFileDrop}
              renderRow={(row) => folderEdit === "rename" && row.id === effectiveScope ? field(row.name) : row.name}
              insertion={folderEdit === "create"
                ? { parentId: selectedFolder ? effectiveScope : null, node: field() } : null} />
          </ResourceViewProvider>
          {canUpload ? picker.input : null}
        </>
      );
    },
    [canUpload, folderRead.error, folderActions.busy, folderActionsRef, folderEdit, handleFileDrop, openFileId, openFileRoute, picker.input, t],
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
      const busy = folderActions.busy || !controller.rootId
        || (effectiveScope !== ALL_SCOPE && effectiveScope !== TRASH_SCOPE && !selectedFolder);
      return {
        actions: [{
          icon: "plus", label: t("new.menu"), text: true, disabled: busy,
          items: [
            { icon: "folder", label: t("new.folder"), onClick: () => setFolderEdit("create") },
            ...(canUpload ? [{ icon: "upload", label: t("new.upload"), onClick: picker.open, disabled: !controller.uploadTarget }] : []),
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
    [canUpload, confirm, folderActions.busy, folderActionsRef, picker.open, t],
  );
  const renderNavigator = useCallback((controller: ScopedStorageController, selector: ReactNode) => {
    const storage = storageController(controller);
    return <>
      <PaneToolbar {...explorerToolbar(storage)} selector={selector} />
      {renderTree(storage)}
    </>;
  }, [explorerToolbar, renderTree]);
  return (
    <ScopedExplorerPane<StorageDrive, StorageTreeRow>
      roots={drives}
      getRootId={driveRootId}
      getRootLabel={driveRootLabel}
      controller={controller}
      navigatorLabel={t("nav.label")}
      rootPicker={driveRootPicker}
      renderTree={renderNavigator}
      renderContentWithoutRoots={openFileId !== null || Boolean(requestedFolder)}
      loading={initialDrives.list.fetching && drives.length === 0}
      loadingContent={<LoadingPanel message={t("loading")} />}
      emptyContent={
        <EmptyState
          fill
          icon="drive"
          title={
            (driveOptions.list.error ?? initialDrives.list.error)
              ? t("drives.unavailableTitle")
              : t("drives.emptyTitle")
          }
          description={
            driveOptions.list.error ?? initialDrives.list.error ?? t("drives.emptyDescription")
          }
        />
      }
    >
      {(controller) => children(storageController(controller))}
    </ScopedExplorerPane>
  );
}

function StorageFolders({ drive, selectedId, revealRows, onSelect, onFileDrop, renderRow, insertion }: {
  drive: string; selectedId: string | undefined; revealRows: readonly StorageTreeRow[];
  onSelect: (row: StorageTreeRow) => void; onFileDrop: (id: string, file: FileDragData) => void;
  renderRow: (row: StorageTreeRow) => ReactNode;
  insertion: { parentId: string | null; node: ReactNode } | null;
}): ReactElement | null {
  const t = useStorageT();
  const metadata = useModelMetadata(FOLDER_MODEL);
  const source = useMemo(() => metadata ? folderSource(metadata, drive) : null, [drive, metadata]);
  if (!source) return null;
  return <CollectionTreeView<StorageTreeRow> source={source} resource={FOLDER_MODEL} presentation="workspace" pane={{}}
    columns={[{ field: "name", header: t("folder.nameLabel") }]} textFilterField="name" search={{ box: true }}
    rowKey="id" parent="parent" label="name" icon="icon" hasChildren="hasChildren"
    selectedId={selectedId} revealRows={revealRows} onRowClick={onSelect} renderRow={renderRow} insertion={insertion}
    dropAccept={STORAGE_FILE_DND} canDropOnNode={(_id, row) => row.kind !== "file"}
    onNodeDrop={(id, payload) => { const file = fileDragData(payload.data); if (file) onFileDrop(id, file); }} />;
}
