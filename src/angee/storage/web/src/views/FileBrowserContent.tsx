import type { ReactElement } from "react";

import type { ResourceFilter } from "@angee/metadata";
import { Column, Glyph, List, UploadDropTarget, formatSize, type ListProps, type ResourceListSnapshot } from "@angee/ui";

import { useStorageT } from "../i18n";
import { fileDragPayload, useFileTypeLabel, type StorageFileRow } from "../data/file-rows";
import type { StorageUpload, UploadTarget } from "../data/use-upload";
import { fileGalleryCard } from "./file-columns";
import { StorageUploadTasks } from "./StorageUploadTasks";

export interface FileBrowserContentProps {
  baseFilter: ResourceFilter<"storage.File">;
  /** Detail route for a clicked row — the list renders each row as a link. */
  rowHref: ListProps<StorageFileRow>["rowHref"];
  /** Bulk actions rendered in the selection bar when files are selected. */
  bulkActions: ListProps<StorageFileRow>["bulkActions"];
  onListStateChange: (state: ResourceListSnapshot<StorageFileRow>) => void;
  uploads: StorageUpload;
  uploadTarget: UploadTarget | null;
}

const FILE_LIST_FIELDS = [
  "filename",
  "is_trashed",
  "url",
  "drive",
  "folder",
  "mime_type.mime_type",
  "mime_type.category",
  "mime_type.icon_key",
] as const;

/**
 * The file list plus its upload surface: an Upload button and a drop target over
 * the whole pane, with a progress strip while files are in flight. Dropping or
 * picking files runs the upload protocol against the current drive/folder.
 */
export function FileBrowserContent({
  baseFilter,
  rowHref,
  bulkActions,
  onListStateChange,
  uploads,
  uploadTarget,
}: FileBrowserContentProps): ReactElement {
  const t = useStorageT();
  const fileTypeLabel = useFileTypeLabel();

  function startUpload(files: readonly File[]): void {
    if (!uploadTarget || files.length === 0) return;
    uploads.upload(Array.from(files), uploadTarget);
  }

  const list = (
    <List<StorageFileRow>
      resource="storage.File"
      baseFilter={baseFilter}
      fields={FILE_LIST_FIELDS}
      rowHref={rowHref}
      bulkActions={bulkActions}
      draggableRow={fileDragPayload}
      renderCard={fileGalleryCard}
      onListStateChange={onListStateChange}
      // Upload is the navigator's New ▾ verb; the list keeps the drop target and the empty hint.
      emptyContent={uploadTarget ? t("list.emptyUpload") : t("list.empty")}
    >
      <Column<StorageFileRow>
        field="title"
        header={t("column.name")}
        render={(row) => (
          <span className="flex min-w-0 items-center gap-2">
            <Glyph
              decorative
              name={row.mime_type?.icon_key || "file"}
              fallbackName="file"
              className="text-fg-muted"
            />
            <span className="truncate font-medium text-fg">
              {row.title || row.filename}
            </span>
          </span>
        )}
      />
      <Column<StorageFileRow>
        field="mime_type.label"
        header={t("column.type")}
        render={(row) => fileTypeLabel(row)}
      />
      <Column<StorageFileRow>
        field="upload_state"
        header={t("column.stage")}
        widget="statusBadge"
      />
      <Column<StorageFileRow>
        field="visibility"
        header={t("column.visibility")}
        widget="statusBadge"
      />
      <Column<StorageFileRow>
        field="size_bytes"
        header={t("column.size")}
        align="right"
        render={(row) => (
          <span className="whitespace-nowrap tabular-nums text-fg-muted">
            {formatSize(row.size_bytes)}
          </span>
        )}
      />
      <Column<StorageFileRow>
        field="id"
        header={t("column.count")}
        aggregate="count"
        align="right"
        // The count means something on a group footer only; a row's own "1" is noise.
        render={() => null}
      />
      <Column<StorageFileRow>
        field="created_by_label"
        header={t("column.author")}
        widget="userRef"
      />
      <Column<StorageFileRow> field="created_at" header={t("column.created")} widget="date" />
      <Column<StorageFileRow> field="updated_at" header={t("column.modified")} widget="date" />
      <Column<StorageFileRow> field="tags" hiddenByDefault />
    </List>
  );

  return (
    <UploadDropTarget
      className="relative flex h-full min-h-0 flex-col"
      disabled={!uploadTarget}
      overlay={t("upload.dropOverlay")}
      onFiles={startUpload}
    >
      {uploads.tasks.length > 0 ? (
        <StorageUploadTasks uploads={uploads} t={t} />
      ) : null}
      <div className="min-h-0 flex-1">
        {list}
      </div>
    </UploadDropTarget>
  );
}
