import { useAuthoredQuery } from "@angee/refine";
import { useCallback, useEffect, useMemo, useState, type ReactElement } from "react";
import { useNavigate, useRouterState, useSearch } from "@tanstack/react-router";

import {
  Button, buttonVariants, ControlBand, EmptyState, formatSize, Glyph, LoadingPanel, PreviewPane, RecordPager, ScopedExplorerPane, recordPath, SelectionBarAction, SurfaceHeader, TreeView, useBreadcrumbLeafLabel, useChatterContent, useConfirm, useLatestRef, useListRecordNavigation, useRouteRecordId, type ChatterTab, type FieldDescriptor, type PreviewFile, type ScopedExplorerController } from "@angee/ui";

import {
  StorageBackends,
  StorageDrives,
  StorageFileById,
  StorageFolderChildren,
  StorageFolderRoots,
  type StorageDrive,
  type StorageFile,
  type StorageFolder,
} from "../data/documents";
import {
  ALL_SCOPE,
  FOLDER_SCOPE_PARAM,
  STORAGE_FILE_DND,
  TRASH_SCOPE,
  folderScopeFromSearch,
  folderScopeToParam,
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

// Drives and backends are small catalogues. Files page through their resource
// and folders load lazily per parent for the active drive.
const STORAGE_CATALOGUE_LIMIT = 500;
const FILE_MODEL = "storage.File";
const FOLDER_MODEL = "storage.Folder";
const ALL_FILES_DEFAULT_GROUP = { field: "folder" } as const;
// A single parent's children (and the drive's top level) come back in one
// request, capped here. The Tree is not virtualized, so rendering far more than
// this under one parent would be the real cost; the cap keeps each per-parent
// fetch and render bounded. NOTE: a level with more than this many folders is
// truncated (no continuation) — acceptable for now, since virtualization + paged
// children is the tracked follow-up, not silent completeness.
const FOLDER_PAGE_LIMIT = 5000;

// Stable field projections for the drive tree roots: module-scope so the
// explorer's option list keeps a stable identity (the navigator is published
// into the shell primary pane and must not churn on every render).
const driveRootId = (drive: StorageDrive): string => drive.id;
const driveRootLabel = (drive: StorageDrive): string => drive.name || drive.slug;

type StorageExplorerController = ScopedExplorerController<
  StorageDrive,
  StorageTreeRow
>;

/**
 * The file browser: it publishes a folder navigator into the console shell's
 * primary pane and an open file's metadata into the chatter's details tab, and
 * renders the scoped file list or the open-file preview as its content (the
 * file's download/trash/restore verbs and the record pager ride the shell's
 * control band). The drive switcher scopes the folder tree and server-backed
 * file list, and a row click opens an independently fetched file preview route.
 */
export function StoragePage(): ReactElement {
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

  // The open file is route state: `/storage/$id` swaps the content to the large
  // preview and the aside to editable metadata; `/storage` is the list.
  const navigate = useNavigate();
  // The navigator scope (All files / Trash / a folder) lives in the URL beside
  // the `group` view param, so it is deep-linkable and back/forward works.
  const search = useSearch({ strict: false }) as Readonly<Record<string, unknown>>;
  const folderScope = useMemo(() => folderScopeFromSearch(search), [search]);
  // Write the scope to the address bar (and close any open preview by landing on
  // the list route), preserving every other param — `group` above all.
  const selectScope = useCallback(
    (scope: string | null) => {
      const folder = folderScopeToParam(scope ?? ALL_SCOPE);
      void navigate({
        to: "/storage",
        // All files is the absent default: drop the `folder` key rather than
        // writing `undefined`, keeping the address bar clean and every other
        // param intact — the same typed updater shape as openFileRoute/closeDetail.
        search: (current: Record<string, unknown>) => {
          const next = { ...current };
          if (folder === undefined) delete next[FOLDER_SCOPE_PARAM];
          else next[FOLDER_SCOPE_PARAM] = folder;
          return next;
        },
      });
    },
    [navigate],
  );
  const openFileId = useRouteRecordId() ?? null;
  const openFileQuery = useAuthoredQuery(
    StorageFileById,
    { id: openFileId ?? "" },
    { enabled: openFileId !== null, models: [FILE_MODEL] },
  );
  const openFile = openFileQuery.data?.files_by_pk ?? null;
  const [activeDriveId, setActiveDriveId] = useState<string | null>(null);
  const folderDriveId =
    openFile?.drive ?? activeDriveId ?? drives[0]?.id ?? "";
  useEffect(() => {
    if (openFile?.drive) setActiveDriveId(openFile.drive);
  }, [openFile?.drive]);
  // Lazy folder tree: the drive's top-level folders come from `rootsQuery` and
  // stay in the query cache (derived during render, never mirrored into state).
  // Only a folder's children are accumulated — fetched on the first expand and
  // kept in `childrenByParent`, keyed by the expanded folder's id — with
  // `loadedParents` recording which folders have resolved their children.
  const [childrenByParent, setChildrenByParent] = useState<
    ReadonlyMap<string, readonly StorageFolder[]>
  >(() => new Map());
  const [loadedParents, setLoadedParents] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  // A FIFO of folders awaiting a children fetch; the head drives the single
  // children query, so one parent is in flight at a time and expansions queue up.
  const [expandQueue, setExpandQueue] = useState<readonly string[]>([]);
  const nextParent = expandQueue[0] ?? null;

  // Switching drives invalidates every loaded folder; start the accumulator over.
  useEffect(() => {
    setChildrenByParent(new Map<string, readonly StorageFolder[]>());
    setLoadedParents(new Set<string>());
    setExpandQueue([]);
  }, [folderDriveId]);

  const rootsQuery = useAuthoredQuery(
    StorageFolderRoots,
    { drive: folderDriveId, limit: FOLDER_PAGE_LIMIT },
    { enabled: folderDriveId !== "", models: [FOLDER_MODEL] },
  );

  const childrenQuery = useAuthoredQuery(
    StorageFolderChildren,
    { drive: folderDriveId, parent: nextParent ?? "", limit: FOLDER_PAGE_LIMIT },
    {
      enabled: folderDriveId !== "" && nextParent !== null,
      models: [FOLDER_MODEL],
    },
  );
  const childFolders = childrenQuery.data?.folders;
  const childrenFetching = childrenQuery.fetching;
  const childrenError = childrenQuery.error;
  // Append a resolved parent's children to the accumulator, then dequeue it. The
  // `fetching` gate keeps `keepPreviousData` placeholders out, and the parent
  // check ignores any stale rows that belong to a different parent. Dequeuing
  // clears `nextParent`, so this settles after one pass per expanded folder.
  useEffect(() => {
    if (nextParent === null || childrenFetching) return;
    // A failed fetch (permission error, dropped socket, timeout after react-
    // query's retries) must not wedge the queue: drop the head so the rest keeps
    // draining. The parent is deliberately left out of `loadedParents`, so its
    // optimistic caret stays and a later expand re-enqueues it — the fetch is
    // retryable rather than stuck spinning forever.
    if (childrenError) {
      setExpandQueue((prev) => (prev[0] === nextParent ? prev.slice(1) : prev));
      return;
    }
    if (!childFolders) return;
    if (childFolders.length > 0 && (childFolders[0]?.parent ?? "") !== nextParent) {
      return;
    }
    setChildrenByParent((prev) => new Map(prev).set(nextParent, childFolders));
    setLoadedParents((prev) => new Set(prev).add(nextParent));
    setExpandQueue((prev) => (prev[0] === nextParent ? prev.slice(1) : prev));
  }, [nextParent, childrenFetching, childrenError, childFolders]);

  // Expanding an unloaded folder enqueues it (deduped against the queue and the
  // already-loaded set, read through a ref to keep the callback stable).
  const loadedParentsRef = useLatestRef(loadedParents);
  const handleExpandFolder = useCallback(
    (nodeId: string) => {
      if (loadedParentsRef.current.has(nodeId)) return;
      setExpandQueue((prev) =>
        prev.includes(nodeId) ? prev : [...prev, nodeId],
      );
    },
    [loadedParentsRef],
  );
  // A folder write can change the top level and any expanded parent's children:
  // refresh the roots (they flow straight from the query) and collapse the loaded
  // children so the next expand refetches them.
  const refetchRoots = rootsQuery.refetch;
  const handleFolderMutation = useCallback(() => {
    refetchRoots();
    setChildrenByParent(new Map<string, readonly StorageFolder[]>());
    setLoadedParents(new Set<string>());
    setExpandQueue([]);
  }, [refetchRoots]);
  const rootFolders = rootsQuery.data?.folders;
  const accumulatedFolders = useMemo(
    () => [
      ...(rootFolders ?? []),
      ...Array.from(childrenByParent.values()).flat(),
    ],
    [rootFolders, childrenByParent],
  );
  const closeDetail = useCallback(() => {
    void navigate({
      to: "/storage",
      search: (current: Record<string, unknown>) => current,
    });
  }, [navigate]);

  useBreadcrumbLeafLabel(openFile ? openFile.title || openFile.filename : null);
  const openFileRoute = useCallback(
    (id: string) => {
      // Keep the folder/group scope in the URL across the preview round-trip so
      // closing the file returns to the same folder.
      void navigate({
        to: recordPath("/storage", id),
        search: (current: Record<string, unknown>) => current,
      });
    },
    [navigate],
  );
  const getTreeRows = useCallback(
    (rootId: string) =>
      folderTreeRows(
        accumulatedFolders,
        rootId,
        loadedParents,
        openFile,
        nextParent,
      ),
    [accumulatedFolders, loadedParents, nextParent, openFile],
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
  const uploads = useStorageUpload();
  const refreshOpenFile = useCallback(() => {
    if (openFileId) openFileQuery.refetch();
  }, [openFileId, openFileQuery.refetch]);
  const fileActions = useFileActions({ onChanged: refreshOpenFile });
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
      create: { resource: "Drive", fields: driveCreateFields },
      onCreated: (id: string) => {
        setActiveDriveId(id);
        void refetchDrives();
      },
    }),
    [driveCreateFields, refetchDrives, t],
  );
  const renderTree = useCallback(
    (controller: StorageExplorerController) => (
      <TreeView<StorageTreeRow>
        rows={controller.treeRows}
        parent="parent"
        label="name"
        rowKey="id"
        icon="icon"
        hasChildren="hasChildren"
        loading="loading"
        onExpand={handleExpandFolder}
        selectedId={openFile?.id ?? (controller.selectedId ?? ALL_SCOPE)}
        onSelect={(row) => {
          if (row.kind === "file") {
            openFileRoute(row.id);
            return;
          }
          // Writes the scope to the URL (via onSelectedIdChange) and lands on the
          // list route, closing any open preview.
          controller.setSelectedId(row.id);
        }}
        dropAccept={STORAGE_FILE_DND}
        canDropOnNode={(_nodeId, row) => row.kind !== "file"}
        onNodeDrop={(nodeId, payload) =>
          handleFileDrop(nodeId, payload.data as FileDragData)
        }
        className="min-h-0 flex-1 overflow-auto"
      />
    ),
    [handleExpandFolder, handleFileDrop, openFile, openFileRoute],
  );
  const renderNavigatorFooter = useCallback(
    (controller: StorageExplorerController) => {
      const effectiveScope = controller.selectedId ?? ALL_SCOPE;
      const selectedFolder =
        effectiveScope !== ALL_SCOPE && effectiveScope !== TRASH_SCOPE
          ? controller.selectedRow
          : undefined;
      const createFolder = (name: string): void => {
        if (!controller.rootId) return;
        const parent =
          effectiveScope === ALL_SCOPE || effectiveScope === TRASH_SCOPE
            ? null
            : effectiveScope;
        void folderActionsRef.current.create({
          drive: controller.rootId,
          name,
          parent,
        });
      };
      const renameFolder = (name: string): void => {
        void folderActionsRef.current.rename(effectiveScope, name);
      };
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
      return (
        <>
          {selectedFolder ? (
            <SelectedFolderControl
              key={selectedFolder.id}
              name={selectedFolder.name}
              busy={folderActions.busy}
              onRename={renameFolder}
              onDelete={deleteFolder}
            />
          ) : null}
          <NewFolderControl busy={folderActions.busy} onCreate={createFolder} />
        </>
      );
    },
    [confirm, folderActions.busy, folderActionsRef, t],
  );

  // The open file's metadata, published as an additive `details` tab into the
  // chatter; nothing published renders the default chatter tabs.
  const detailsTab = useMemo<readonly ChatterTab[]>(
    () =>
      openFile
        ? [
            {
              id: "details",
              label: t("file.detailsTab"),
              icon: "info",
              children: (
                <FileDetail
                  file={openFile}
                  onChanged={openFileQuery.refetch}
                  compact
                />
              ),
            },
          ]
        : [],
    [openFile, openFileQuery.refetch, t],
  );
  const chatter = useMemo(() => ({ tabs: detailsTab }), [detailsTab]);
  useChatterContent(chatter);
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
      renderNavigatorFooter={renderNavigatorFooter}
      loading={drivesQuery.fetching && drives.length === 0}
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
      {(controller) => (
        <StorageExplorerContent
          controller={controller}
          openFileId={openFileId}
          openFile={openFile}
          openFileFetching={openFileQuery.fetching}
          uploads={uploads}
          fileActions={fileActions}
          closeDetail={closeDetail}
          onOpenFile={openFileRoute}
        />
      )}
    </ScopedExplorerPane>
  );
}

function StorageExplorerContent({
  controller,
  openFileId,
  openFile,
  openFileFetching,
  uploads,
  fileActions,
  closeDetail,
  onOpenFile,
}: {
  controller: StorageExplorerController;
  openFileId: string | null;
  openFile: StorageFile | null;
  openFileFetching: boolean;
  uploads: ReturnType<typeof useStorageUpload>;
  fileActions: ReturnType<typeof useFileActions>;
  closeDetail: () => void;
  onOpenFile: (id: string) => void;
}): ReactElement {
  const t = useStorageT();
  const driveId = controller.rootId;
  const effectiveScope = controller.selectedId ?? ALL_SCOPE;
  const baseFilter = useMemo(
    () =>
      effectiveScope === TRASH_SCOPE
        ? {
            drive: { exact: driveId },
            is_trashed: { exact: true },
          }
        : effectiveScope === ALL_SCOPE
          ? {
              drive: { exact: driveId },
              is_trashed: { exact: false },
            }
          : {
              drive: { exact: driveId },
              is_trashed: { exact: false },
              folder: { exact: effectiveScope },
            },
    [driveId, effectiveScope],
  );
  const defaultGroup =
    effectiveScope === ALL_SCOPE ? ALL_FILES_DEFAULT_GROUP : null;
  const {
    navigationScope,
    navigation: fileNavigation,
    onListStateChange,
  } = useListRecordNavigation<StorageFileRow>({
    recordId: openFileId,
    onSelect: onOpenFile,
  });
  // Carry the current scope (folder/group) into the row's detail href so opening
  // a file — by click, middle-click, or copy-link — keeps the address-bar scope.
  const searchStr = useRouterState({
    select: (state) => state.location.searchStr,
  });
  const rowHref = useCallback(
    (row: StorageFileRow) => `${recordPath("/storage", row.id)}${searchStr}`,
    [searchStr],
  );
  // The selection bar's bulk verbs: Restore in the Trash scope, else Trash.
  const renderBulkActions = useCallback(
    (ids: ReadonlySet<string>, clear: () => void) =>
      effectiveScope === TRASH_SCOPE ? (
        <SelectionBarAction
          surface="brand"
          pending={fileActions.busy}
          onClick={() => void fileActions.restoreMany(ids).then(clear)}
        >
          <Glyph name="restore" />
          {t("bulk.restore")}
        </SelectionBarAction>
      ) : (
        <SelectionBarAction
          surface="brand"
          pending={fileActions.busy}
          onClick={() => void fileActions.trashMany(ids).then(clear)}
        >
          <Glyph name="trash" />
          {t("bulk.trash")}
        </SelectionBarAction>
      ),
    [effectiveScope, fileActions, t],
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

  return (
    <>
      {openFile ? (
        <ControlBand>
          {!openFile.is_trashed && openFile.url !== "" ? (
            <a
              className={buttonVariants({ variant: "secondary", size: "sm" })}
              href={openFile.url}
              download={openFile.filename}
            >
              <Glyph name="download" />
              {t("file.download")}
            </a>
          ) : null}
          {openFile.is_trashed ? (
            <Button
              type="button"
              size="sm"
              variant="secondary"
              loading={fileActions.busy}
              onClick={() => void fileActions.restore(openFile.id)}
            >
              <Glyph name="restore" />
              {t("file.restore")}
            </Button>
          ) : (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              loading={fileActions.busy}
              onClick={() =>
                void fileActions.trash(openFile.id).then(closeDetail)
              }
            >
              <Glyph name="trash" />
              {t("file.trash")}
            </Button>
          )}
          {fileNavigation ? (
            <div className="ml-auto">
              <RecordPager navigation={fileNavigation} />
            </div>
          ) : null}
        </ControlBand>
      ) : null}
      {openFileId ? (
        openFile ? (
          <FilePreviewFrame file={openFile} />
        ) : openFileFetching ? (
          <LoadingPanel message={t("loadingFile")} />
        ) : (
          <EmptyState
            fill
            icon="file"
            title={t("file.notFoundTitle")}
            description={t("file.notFoundDescription")}
          />
        )
      ) : (
        <FileBrowserContent
          baseFilter={baseFilter}
          defaultGroup={defaultGroup}
          rowHref={rowHref}
          bulkActions={renderBulkActions}
          onListStateChange={onListStateChange}
          uploads={uploads}
          uploadTarget={uploadTarget}
          canUpload={canUpload}
        />
      )}
      {openFileId && navigationScope ? (
        <FileBrowserContent
          hidden
          baseFilter={baseFilter}
          defaultGroup={defaultGroup}
          rowHref={rowHref}
          bulkActions={renderBulkActions}
          onListStateChange={onListStateChange}
          navigationScope={navigationScope}
          uploads={uploads}
          uploadTarget={uploadTarget}
          canUpload={canUpload}
        />
      ) : null}
    </>
  );
}

function FilePreviewFrame({ file }: { file: StorageFile }): ReactElement {
  const t = useStorageT();
  // The file's verbs (download, trash/restore) live in the shell control band,
  // beside the preview; this frame just titles and renders the content.
  return (
    <div className="flex h-full min-h-0 flex-col bg-canvas">
      <SurfaceHeader
        density="compact"
        headingLevel={2}
        icon={file.mime_type?.icon_key || "file"}
        title={file.title || file.filename}
        subtitle={t("file.subtitle", {
          type:
            file.mime_type?.label ||
            file.mime_type?.mime_type ||
            t("file.unknownType"),
          size: formatSize(file.size_bytes),
        })}
      />
      <div className="min-h-0 flex-1 overflow-hidden p-3">
        <FilePreview file={file} />
      </div>
    </div>
  );
}

function FilePreview({ file }: { file: StorageFile }): ReactElement {
  const t = useStorageT();
  const previewFile: PreviewFile = {
    url: file.url,
    name: file.filename,
    mime: file.mime_type?.mime_type ?? null,
    size: file.size_bytes,
  };
  return (
    <PreviewPane
      file={previewFile}
      fallback={
        <EmptyState
          icon="file"
          title={file.title || file.filename}
          description={t("preview.unsupported")}
        />
      }
    />
  );
}
