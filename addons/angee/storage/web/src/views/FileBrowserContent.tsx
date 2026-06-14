import { useMemo, useRef, useState, type ReactElement, type ReactNode } from "react";

import { Button, Glyph, RowsListView, cn } from "@angee/base";

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
  const [dragging, setDragging] = useState(false);
  const columns = useMemo(() => fileColumns(t), [t]);

  function startUpload(files: FileList | null): void {
    if (!canUpload || !files || files.length === 0) return;
    uploads.upload(Array.from(files), uploadTarget);
  }

  return (
    <div
      className="relative flex h-full min-h-0 flex-col"
      onDragOver={(event) => {
        if (!canUpload) return;
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(event) => {
        if (event.currentTarget.contains(event.relatedTarget as Node)) return;
        setDragging(false);
      }}
      onDrop={(event) => {
        event.preventDefault();
        setDragging(false);
        startUpload(event.dataTransfer.files);
      }}
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
      {dragging ? (
        <div className="pointer-events-none absolute inset-0 grid place-content-center bg-brand-soft/50 text-15 font-medium text-brand-text">
          {t("storage.upload.dropOverlay")}
        </div>
      ) : null}
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
    </div>
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
        <span className="text-2xs font-semibold uppercase tracking-wide text-fg-muted">
          {t("storage.upload.heading")}
        </span>
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
