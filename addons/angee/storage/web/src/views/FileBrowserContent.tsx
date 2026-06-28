import { useMemo, useRef, type ReactElement, type ReactNode } from "react";

import { Button, Glyph, RowsListView, SectionEyebrow, UploadDropTarget, cn } from "@angee/ui";

import { useStorageT } from "../i18n";
import { fileDragPayload, type StorageFileRow } from "../data/file-rows";
import type { StorageUpload, UploadStatus, UploadTarget, UploadTask } from "../data/use-upload";
import { fileColumns, fileGalleryCard } from "./file-columns";

type Translate = (key: string) => string;

/** Upload-task status → its label. `t` is threaded in from the rendering
 * component; this map is not a component and cannot call the hook itself. */
function statusLabel(status: UploadStatus, t: Translate): string {
  return t(`storage.upload.status.${status}`);
}

export interface FileBrowserContentProps {
  rows: readonly StorageFileRow[];
  fetching: boolean;
  error: Error | null;
  /** Detail route for a clicked row — the list renders each row as a link. */
  rowHref: (row: StorageFileRow) => string;
  /** Bulk actions rendered in the selection bar when files are selected. */
  bulkActions: (selectedIds: ReadonlySet<string>, clear: () => void) => ReactNode;
  uploads: StorageUpload;
  uploadTarget: UploadTarget;
  canUpload: boolean;
}

/**
 * The file list plus its upload surface: an Upload button and a drop target over
 * the whole pane, with a progress strip while files are in flight. Dropping or
 * picking files runs the upload protocol against the current drive/folder.
 */
export function FileBrowserContent({
  rows,
  fetching,
  error,
  rowHref,
  bulkActions,
  uploads,
  uploadTarget,
  canUpload,
}: FileBrowserContentProps): ReactElement {
  const t = useStorageT();
  const inputRef = useRef<HTMLInputElement>(null);
  const columns = useMemo(() => fileColumns(t), [t]);

  function startUpload(files: FileList | readonly File[] | null): void {
    if (!canUpload || !files || files.length === 0) return;
    uploads.upload(Array.from(files), uploadTarget);
  }

  return (
    <UploadDropTarget
      className="relative flex h-full min-h-0 flex-col"
      disabled={!canUpload}
      overlay={t("storage.upload.dropOverlay")}
      onFiles={startUpload}
    >
      {uploads.tasks.length > 0 ? (
        <UploadStrip tasks={uploads.tasks} onClear={uploads.clearFinished} t={t} />
      ) : null}
      <div className="min-h-0 flex-1">
        <RowsListView
          rows={rows}
          columns={columns}
          fetching={fetching}
          error={error}
          rowHref={rowHref}
          selectable
          bulkActions={bulkActions}
          draggableRow={fileDragPayload}
          gallery={{ renderCard: fileGalleryCard }}
          emptyMessage={
            canUpload ? t("storage.list.emptyUpload") : t("storage.list.empty")
          }
          pageSize={50}
          toolbarActions={
            canUpload ? (
              <Button
                type="button"
                size="sm"
                variant="secondary"
                onClick={() => inputRef.current?.click()}
              >
                <Glyph name="attachment" />
                {t("storage.upload.button")}
              </Button>
            ) : undefined
          }
        />
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
          {t("storage.upload.heading")}
        </SectionEyebrow>
        <Button type="button" size="sm" variant="ghost" onClick={onClear}>
          {t("storage.upload.clearFinished")}
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
