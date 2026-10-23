import * as React from "react";
import { Button, PaneLabel, PaneLabelAction, PaneRow, PaneRows, StatusBadge, useRuntimeViewAs, type WidgetOption } from "@angee/ui";
import type { StorageUpload, UploadStatus, UploadTask } from "../data/use-upload";

type Translate = (key: string) => string;

const UPLOAD_STATUSES: readonly UploadStatus[] = ["hashing", "uploading", "finalizing", "done", "deduped", "failed"];

/** The in-flight uploads as pane rows under one label: name · status, Retry as the row verb. */
export function StorageUploadTasks({ uploads, t }: { uploads: StorageUpload; t: Translate }): React.ReactElement {
  const options = React.useMemo<WidgetOption[]>(
    () => UPLOAD_STATUSES.map((value) => ({ value, label: t(`upload.status.${value}`) })),
    [t],
  );
  return (
    <div className="flex max-h-64 flex-col overflow-auto px-2 py-1">
      <PaneLabel count={uploads.tasks.length}
        action={<PaneLabelAction onClick={uploads.clearFinished}>{t("upload.clearFinished")}</PaneLabelAction>}>
        {t("upload.heading")}
      </PaneLabel>
      <PaneRows>
        {uploads.tasks.map((task) => (
          <li key={task.id}>
            <StorageUploadTaskRow task={task} options={options} t={t} onRetry={() => uploads.retry(task.id)} />
          </li>
        ))}
      </PaneRows>
    </div>
  );
}

function StorageUploadTaskRow({ task, options, t, onRetry }: {
  task: UploadTask; options: readonly WidgetOption[]; t: Translate; onRetry?: () => void;
}): React.ReactElement {
  const preview = useRuntimeViewAs();
  return (
    <PaneRow icon="file" title={task.name} sub={task.status === "failed" ? task.error : undefined}
      meta={<StatusBadge value={task.status} options={options} display="dot" />}
      verb={task.status === "failed" && onRetry ? (
        <Button type="button" size="sm" variant="ghost" disabled={Boolean(preview.viewAs || preview.pending)} onClick={onRetry}>
          {t("upload.retry")}
        </Button>
      ) : undefined} />
  );
}
