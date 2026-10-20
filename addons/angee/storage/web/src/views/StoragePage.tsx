import { useAuthoredQuery } from "@angee/refine";
import { useModelMetadata, type DataResourceMetadata } from "@angee/metadata";
import { useCallback, useMemo, type ReactElement } from "react";
import { useNavigate, useRouterState } from "@tanstack/react-router";
import {
  parseRecordNavigationScope, recordNavigationHref, recordNavigationSearch, ResourceViewProvider,
  useBreadcrumbLeafLabel, useBreadcrumbCollectionLink, useListRecordNavigation,
  useRouteHref, useRouteRecordId, useRouteSearch, type ListViewNavigationScope,
} from "@angee/ui";

import { StorageFileById } from "../data/documents";
import { ALL_SCOPE, FOLDER_SCOPE_PARAM, folderScopeFromSearch, folderScopeToParam, type StorageFileRow } from "../data/file-rows";
import { useFileActions } from "../data/use-file-actions";
import { useStorageUpload } from "../data/use-upload";
import { FilePreviewFrame } from "./FilePreviewFrame";
import { filePreviewPageFromSearch } from "./FilePreview";
import { StorageExplorer, type StorageExplorerController } from "./StorageExplorer";
import { StorageListContent } from "./StorageListContent";

const FILE_MODEL = "storage.File";
const FILE_LIST_INITIAL_STATE = { pageSize: 50, sorting: [{ id: "updated_at", desc: true }] };
const ALL_FILES_DEFAULT_GROUP = { field: "folder" } as const;

/** URL-owned file navigation composes the explorer, file frame and scoped list. */
export function StoragePage(): ReactElement {
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  const filesHref = routeHref("storage.files");
  // The navigator scope (All files / Trash / a folder) lives in the URL beside
  // the `group` view param, so it is deep-linkable and back/forward works.
  const search = useRouteSearch();
  const fileResource = useModelMetadata(FILE_MODEL)?.resource;
  const navigationScope = useMemo(
    () => parseRecordNavigationScope(search, fileResource),
    [search, fileResource],
  );
  const folderScope = useMemo(() => folderScopeFromSearch(search), [search]);
  // Write the scope to the address bar (and close any open preview by landing on
  // the list route), preserving every other param — `group` above all.
  const selectScope = useCallback(
    (scope: string | null) => {
      const folder = folderScopeToParam(scope ?? ALL_SCOPE);
      void navigate({
        to: filesHref,
        // All files is the absent default: drop the `folder` key rather than
        // writing `undefined`, keeping the address bar clean and every other
        // param intact — the same typed updater shape as openFileRoute/closeDetail.
        search: (current: Record<string, unknown>) => {
          const next = recordNavigationSearch(current, fileResource, null);
          if (folder === undefined) delete next[FOLDER_SCOPE_PARAM];
          else next[FOLDER_SCOPE_PARAM] = folder;
          return next;
        },
      });
    },
    [filesHref, navigate, fileResource],
  );
  const openFileId = useRouteRecordId() ?? null;
  const openFilePreviewPage = openFileId
    ? filePreviewPageFromSearch(search, openFileId)
    : null;
  const searchStr = useRouterState({ select: (state) => state.location.searchStr });
  useBreadcrumbCollectionLink(
    filesHref,
    openFileId === null ? null
      : recordNavigationHref(routeHref("storage.files", {}, searchStr), fileResource, null),
  );
  const openFileQuery = useAuthoredQuery(
    StorageFileById,
    { id: openFileId ?? "" },
    { enabled: openFileId !== null, models: [FILE_MODEL] },
  );
  const openFile = openFileQuery.data?.files_by_pk ?? null;
  const closeDetail = useCallback(() => {
    void navigate({
      to: filesHref,
      search: (current: Record<string, unknown>) =>
        recordNavigationSearch(current, fileResource, null),
    });
  }, [filesHref, navigate, fileResource]);

  useBreadcrumbLeafLabel(openFile ? openFile.title || openFile.filename : null);
  const openFileRoute = useCallback(
    (id: string, scope?: ListViewNavigationScope) => {
      // Keep the folder/group scope in the URL across the preview round-trip so
      // closing the file returns to the same folder.
      void navigate({
        to: routeHref("storage.file", { id }),
        search: (current: Record<string, unknown>) =>
          recordNavigationSearch(current, fileResource, scope ?? null),
      });
    },
    [navigate, routeHref, fileResource],
  );
  const uploads = useStorageUpload();
  const refreshOpenFile = useCallback(() => {
    if (openFileId) openFileQuery.refetch();
  }, [openFileId, openFileQuery.refetch]);
  const fileActions = useFileActions({ onChanged: refreshOpenFile });

  return <StorageExplorer openFile={openFile} openFileId={openFileId} folderScope={folderScope}
    selectScope={selectScope} openFileRoute={openFileRoute} fileActions={fileActions}>
    {(controller) => <ResourceViewProvider resource={FILE_MODEL} initialState={{ ...FILE_LIST_INITIAL_STATE,
      groupStack: (controller.selectedId ?? ALL_SCOPE) === ALL_SCOPE ? [ALL_FILES_DEFAULT_GROUP] : [],
    }}>
      <StoragePageContent controller={controller} fileResource={fileResource} navigationScope={navigationScope}
        openFileId={openFileId} openFilePreviewPage={openFilePreviewPage}
        uploads={uploads} fileActions={fileActions} closeDetail={closeDetail} onOpenFile={openFileRoute} />
    </ResourceViewProvider>}
  </StorageExplorer>;
}

function StoragePageContent({
  controller,
  openFileId,
  openFilePreviewPage,
  fileResource,
  navigationScope,
  uploads,
  fileActions,
  closeDetail,
  onOpenFile,
}: {
  controller: StorageExplorerController;
  openFileId: string | null;
  openFilePreviewPage: number | null;
  fileResource: DataResourceMetadata | undefined;
  navigationScope: ListViewNavigationScope | null;
  uploads: ReturnType<typeof useStorageUpload>;
  fileActions: ReturnType<typeof useFileActions>;
  closeDetail: () => void;
  onOpenFile: (id: string, scope?: ListViewNavigationScope) => void;
}): ReactElement {
  const {
    navigation: fileNavigation,
    onListStateChange,
  } = useListRecordNavigation<StorageFileRow>({
    resource: FILE_MODEL,
    navigationScope,
    recordId: openFileId,
    onSelect: onOpenFile,
  });
  return openFileId ? <FilePreviewFrame id={openFileId} page={openFilePreviewPage}
    fileActions={fileActions} closeDetail={closeDetail} fileNavigation={fileNavigation} /> :
    <StorageListContent controller={controller} fileResource={fileResource} navigationScope={navigationScope}
      uploads={uploads} fileActions={fileActions} onListStateChange={onListStateChange} />;
}
