import { useCallback, useMemo, type ReactElement } from "react";
import { useRouterState } from "@tanstack/react-router";
import type { DataResourceMetadata } from "@angee/metadata";
import { Glyph, recordNavigationHref, SelectionBarAction, useRouteHref, type ListViewNavigationScope, type ResourceListSnapshot } from "@angee/ui";

import { ALL_SCOPE, TRASH_SCOPE, type StorageFileRow } from "../data/file-rows";
import type { FileActions } from "../data/use-file-actions";
import type { StorageUpload } from "../data/use-upload";
import { useStorageT } from "../i18n";
import { FileBrowserContent } from "./FileBrowserContent";
import type { StorageExplorerController } from "./StorageExplorer";

/** The active drive/folder's list scope, row links, bulk verbs and upload destination. */
export function StorageListContent({ controller, fileResource, navigationScope, uploads, fileActions, onListStateChange }: {
  controller: StorageExplorerController;
  fileResource: DataResourceMetadata | undefined;
  navigationScope: ListViewNavigationScope | null;
  uploads: StorageUpload;
  fileActions: FileActions;
  onListStateChange: (state: ResourceListSnapshot<StorageFileRow>) => void;
}): ReactElement {
  const t = useStorageT();
  const routeHref = useRouteHref();
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
  // Carry the current scope (folder/group) into the row's detail href so opening
  // a file — by click, middle-click, or copy-link — keeps the address-bar scope.
  const searchStr = useRouterState({
    select: (state) => state.location.searchStr,
  });
  const rowHref = useCallback(
    (row: StorageFileRow, scope?: ListViewNavigationScope) =>
      recordNavigationHref(
        routeHref("storage.file", { id: row.id }, searchStr),
        fileResource,
        scope ?? navigationScope,
      ),
    [routeHref, searchStr, fileResource, navigationScope],
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
  return <FileBrowserContent baseFilter={baseFilter} rowHref={rowHref} bulkActions={renderBulkActions}
    onListStateChange={onListStateChange} uploads={uploads} uploadTarget={controller.uploadTarget} />;
}
