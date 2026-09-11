import * as React from "react";
import { Button, SectionEyebrow, cn } from "@angee/ui";
import type { StorageUpload, UploadTask } from "../data/use-upload";

type Translate = (key: string) => string;

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
