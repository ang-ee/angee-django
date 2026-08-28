import { useRef, type ReactElement, type ReactNode } from "react";

import type { ResourceFilter } from "@angee/metadata";
import {
  Badge,
  Button,
  Column,
  ControlBandProvider,
  Glyph,
  List,
  SectionEyebrow,
  UploadDropTarget,
  cn,
  formatSize,
  type ListProps,
  type ResourceListSnapshot,
} from "@angee/ui";

import { useStorageT } from "../i18n";
import { fileDragPayload, type StorageFileRow } from "../data/file-rows";
import type { StorageUpload, UploadStatus, UploadTarget, UploadTask } from "../data/use-upload";
import { fileGalleryCard } from "./file-columns";
import { fileStage, formatDate } from "../lib/file-display";

type Translate = (key: string) => string;

/** Upload-task status → its label. `t` is threaded in from the rendering
 * component; this map is not a component and cannot call the hook itself. */
function statusLabel(status: UploadStatus, t: Translate): string {
  return t(`upload.status.${status}`);
}

export interface FileBrowserContentProps {
  baseFilter: ResourceFilter<"storage.File">;
  defaultGroup: ListProps<StorageFileRow>["defaultGroup"];
  /** Detail route for a clicked row — the list renders each row as a link. */
  rowHref: (row: StorageFileRow) => string;
  /** Bulk actions rendered in the selection bar when files are selected. */
  bulkActions: (selectedIds: ReadonlySet<string>, clear: () => void) => ReactNode;
  onListStateChange: (state: ResourceListSnapshot<StorageFileRow>) => void;
  /** Loaded leaf scope replayed while the file preview replaces the visible list. */
  navigationScope?: ListProps<StorageFileRow>["navigationScope"];
  uploads: StorageUpload;
  uploadTarget: UploadTarget;
  canUpload: boolean;
  /** Keep the server list alive behind a preview without publishing its toolbar. */
  hidden?: boolean;
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
  defaultGroup,
  rowHref,
  bulkActions,
  onListStateChange,
  navigationScope,
  uploads,
  uploadTarget,
  canUpload,
  hidden = false,
}: FileBrowserContentProps): ReactElement {
  const t = useStorageT();
  const inputRef = useRef<HTMLInputElement>(null);

  function startUpload(files: FileList | readonly File[] | null): void {
    if (!canUpload || !files || files.length === 0) return;
    uploads.upload(Array.from(files), uploadTarget);
  }

  const list = (
    <List<StorageFileRow>
      resource="storage.File"
      baseFilter={baseFilter}
      defaultGroup={navigationScope ? null : defaultGroup}
      order={{ updated_at: "DESC" }}
      pageSize={50}
      {...(navigationScope ? { navigationScope } : {})}
      fields={FILE_LIST_FIELDS}
      rowHref={rowHref}
      bulkActions={bulkActions}
      draggableRow={fileDragPayload}
      renderCard={fileGalleryCard}
      onListStateChange={onListStateChange}
      emptyContent={canUpload ? t("list.emptyUpload") : t("list.empty")}
      toolbarActions={
        !hidden && canUpload ? (
          <Button
            type="button"
            size="sm"
            variant="secondary"
            onClick={() => inputRef.current?.click()}
          >
            <Glyph name="attachment" />
            {t("upload.button")}
          </Button>
        ) : undefined
      }
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
        render={(row) =>
          row.mime_type?.label || row.mime_type?.mime_type || "—"
        }
      />
      <Column<StorageFileRow>
        field="upload_state"
        header={t("column.stage")}
        render={(row) => {
          const stage = fileStage(row.upload_state, t);
          return <Badge tone={stage.tone}>{stage.label}</Badge>;
        }}
      />
      <Column<StorageFileRow>
        field="size_bytes"
        header={t("column.size")}
        align="right"
        render={(row) => (
          <span className="tabular-nums text-fg-muted">
            {formatSize(row.size_bytes)}
          </span>
        )}
      />
      <Column<StorageFileRow>
        field="id"
        header={t("column.count")}
        aggregate="count"
        align="right"
        render={() => <span className="text-fg-muted">1</span>}
      />
      <Column<StorageFileRow>
        field="created_by_label"
        header={t("column.owner")}
        render={(row) => row.created_by_label || "—"}
      />
      <Column<StorageFileRow>
        field="updated_at"
        header={t("column.modified")}
        render={(row) => (
          <span className="text-fg-muted">{formatDate(row.updated_at)}</span>
        )}
      />
    </List>
  );

  if (hidden) {
    return (
      <ControlBandProvider host={undefined}>
        <div hidden aria-hidden="true">
          {list}
        </div>
      </ControlBandProvider>
    );
  }

  return (
    <UploadDropTarget
      className="relative flex h-full min-h-0 flex-col"
      disabled={!canUpload}
      overlay={t("upload.dropOverlay")}
      onFiles={startUpload}
    >
      {uploads.tasks.length > 0 ? (
        <UploadStrip tasks={uploads.tasks} onClear={uploads.clearFinished} t={t} />
      ) : null}
      <div className="min-h-0 flex-1">
        {list}
      </div>
      <input
        ref={inputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(event) => {
          startUpload(event.target.files);
          event.target.value = "";
        }}
      />
    </UploadDropTarget>
  );
}

function UploadStrip({
  tasks,
  onClear,
  t,
}: {
  tasks: readonly UploadTask[];
  onClear: () => void;
  t: Translate;
}): ReactElement {
  return (
    <div className="flex max-h-32 flex-col gap-1 overflow-auto border-b border-border-subtle bg-sheet-2 px-3 py-2">
      <div className="flex items-center justify-between">
        <SectionEyebrow as="span">
          {t("upload.heading")}
        </SectionEyebrow>
        <Button type="button" size="sm" variant="ghost" onClick={onClear}>
          {t("upload.clearFinished")}
        </Button>
      </div>
      {tasks.map((task) => (
        <div key={task.id} className="flex items-center gap-2 text-13">
          <span className="min-w-0 flex-1 truncate text-fg">{task.name}</span>
          <span
            className={cn(
              "shrink-0 text-2xs",
              task.status === "failed" ? "text-danger-text" : "text-fg-muted",
            )}
            title={task.error}
          >
            {statusLabel(task.status, t)}
          </span>
        </div>
      ))}
    </div>
  );
}
