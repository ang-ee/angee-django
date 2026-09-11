import * as React from "react";

import {
  Button,
  Glyph,
  SectionEyebrow,
  UploadDropTarget,
  cn,
} from "@angee/ui";

import { useStorageT } from "../i18n";
import {
  useStorageUpload,
  type StorageUpload,
  type UploadedFile,
  type UploadTarget,
  type UploadTask,
} from "../data/use-upload";

type Translate = (key: string) => string;

export interface StorageUploadPanelProps {
  accept?: string;
  canUpload?: boolean;
  multiple?: boolean;
  target?: UploadTarget;
  onUploaded?: (files: readonly UploadedFile[]) => void;
  onUploadStart?: (files: readonly File[], target: UploadTarget | undefined) => void;
  onBusyChange?: (busy: boolean) => void;
  heading?: React.ReactNode;
  description?: React.ReactNode;
  emptyLabel?: React.ReactNode;
}

/**
 * Reusable storage-owned upload surface. Consumers receive finalized File ids;
 * hashing, transfer, deduplication, progress, and retryable file selection stay
 * inside the storage boundary.
 */
export function StorageUploadPanel({
  accept,
  canUpload = true,
  multiple = true,
  target,
  onUploaded,
  onUploadStart,
  onBusyChange,
  heading,
  description,
  emptyLabel,
}: StorageUploadPanelProps): React.ReactElement {
  const t = useStorageT();
  const inputRef = React.useRef<HTMLInputElement>(null);
  const uploads = useStorageUpload({ onUploaded });
  const busy = uploads.tasks.some((task) => !["done", "deduped", "failed"].includes(task.status));
  React.useEffect(() => onBusyChange?.(busy), [busy, onBusyChange]);

  const startUpload = React.useCallback((files: FileList | readonly File[] | null) => {
    if (!canUpload || !files?.length) return;
    const selected = Array.from(files);
    onUploadStart?.(selected, target);
    uploads.upload(selected, target);
  }, [canUpload, onUploadStart, target, uploads]);

  return (
    <UploadDropTarget
      className="relative flex min-h-64 flex-col rounded-md border border-border-subtle bg-sheet"
      disabled={!canUpload}
      overlay={t("upload.dropOverlay")}
      onFiles={startUpload}
    >
      <div className="flex items-start justify-between gap-4 border-b border-border-subtle p-4">
        <div>
          <h2 className="font-medium text-fg">{heading ?? t("upload.heading")}</h2>
          {description ? <p className="mt-1 text-13 text-fg-muted">{description}</p> : null}
        </div>
        <Button
          type="button"
          size="sm"
          variant="secondary"
          disabled={!canUpload}
          onClick={() => inputRef.current?.click()}
        >
          <Glyph name="attachment" />
          {t("upload.button")}
        </Button>
      </div>
      {uploads.tasks.length > 0 ? (
        <StorageUploadTasks uploads={uploads} t={t} />
      ) : (
        <div className="flex flex-1 items-center justify-center p-8 text-center text-13 text-fg-muted">
          {emptyLabel ?? t("list.emptyUpload")}
        </div>
      )}
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        multiple={multiple}
        className="hidden"
        onChange={(event) => {
          startUpload(event.target.files);
          event.target.value = "";
        }}
      />
    </UploadDropTarget>
  );
}

export function StorageUploadTasks({ uploads, t }: { uploads: StorageUpload; t: Translate }): React.ReactElement {
  return (
    <div className="flex max-h-64 flex-col gap-1 overflow-auto p-4">
      <div className="mb-1 flex items-center justify-between">
        <SectionEyebrow as="span">{t("upload.heading")}</SectionEyebrow>
        <Button type="button" size="sm" variant="ghost" onClick={uploads.clearFinished}>
          {t("upload.clearFinished")}
        </Button>
      </div>
      {uploads.tasks.map((task) => <StorageUploadTaskRow key={task.id} task={task} t={t} onRetry={() => uploads.retry(task.id)} />)}
    </div>
  );
}

function StorageUploadTaskRow({ task, t, onRetry }: { task: UploadTask; t: Translate; onRetry?: () => void }): React.ReactElement {
  return (
    <div className="flex items-center gap-2 text-13">
      <span className="min-w-0 flex-1 truncate text-fg">{task.name}</span>
      <span
        className={cn("shrink-0 text-2xs", task.status === "failed" ? "text-danger-text" : "text-fg-muted")}
        title={task.error}
      >
        {t(`upload.status.${task.status}`)}
      </span>
      {task.status === "failed" && onRetry ? (
        <Button type="button" size="sm" variant="ghost" onClick={onRetry}>{t("upload.retry")}</Button>
      ) : null}
    </div>
  );
}
