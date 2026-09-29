import { useAuthoredQuery } from "@angee/refine";
import { Button, EmptyState, ErrorBanner, Select, Skeleton, SkeletonStatus, UploadDropTarget, useRuntimeViewAs, type ChatterViewContext } from "@angee/ui";
import { useMemo, useRef, useState, type ReactElement } from "react";

import { STORAGE_CATALOGUE_LIMIT, StorageDrives, StorageRecordFiles } from "./data/documents";
import { useStorageUpload } from "./data/use-upload";
import { useStorageT } from "./i18n";
import { FileRecordPreview } from "./views/FilePreview";
import { StorageUploadTasks } from "./views/StorageUploadTasks";

const ATTACHMENT_MODELS = ["storage.FileAttachment"] as const;

function recordVariables(context: ChatterViewContext) {
  return {
    modelLabel: context.route?.modelLabel ?? "",
    recordId: context.view.kind === "record" ? context.view.sqid ?? "" : "",
  };
}

/** Count uses the same actor-scoped read as the panel. */
export function useRecordFilesCount(context: ChatterViewContext): number | undefined {
  const { modelLabel, recordId } = recordVariables(context);
  const variables = useMemo(() => ({ modelLabel, recordId }), [modelLabel, recordId]);
  const query = useAuthoredQuery(StorageRecordFiles, variables, {
    enabled: Boolean(modelLabel && recordId), models: ATTACHMENT_MODELS,
  });
  const files = query.data?.record_files;
  return files?.available ? files.attachments.length : undefined;
}

/** Record-scoped file list, inline preview, and the existing upload protocol. */
export function RecordFilesPane({ context }: { context: ChatterViewContext }): ReactElement {
  const t = useStorageT();
  const preview = useRuntimeViewAs();
  const { modelLabel, recordId } = recordVariables(context);
  const variables = useMemo(() => ({ modelLabel, recordId }), [modelLabel, recordId]);
  const filesQuery = useAuthoredQuery(StorageRecordFiles, variables, {
    enabled: Boolean(modelLabel && recordId), models: ATTACHMENT_MODELS,
  });
  const drivesQuery = useAuthoredQuery(StorageDrives, { limit: STORAGE_CATALOGUE_LIMIT, offset: 0 }, {
    enabled: Boolean(filesQuery.data?.record_files?.can_upload), models: ["storage.Drive"],
  });
  const uploads = useStorageUpload();
  const inputRef = useRef<HTMLInputElement>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [driveId, setDriveId] = useState("");
  const recordFiles = filesQuery.data?.record_files;
  const drives = drivesQuery.data?.drives ?? [];
  const selectedDriveId = drives.some((drive) => drive.id === driveId) ? driveId : drives[0]?.id ?? "";
  const canUpload = Boolean(recordFiles?.can_upload && selectedDriveId && !preview.viewAs && !preview.pending);

  function startUpload(files: FileList | readonly File[] | null): void {
    if (!files?.length || !canUpload) return;
    uploads.upload(Array.from(files), {
      driveId: selectedDriveId,
      visibility: "RECORD",
      record: { model_label: modelLabel, record_id: recordId },
    });
  }

  if (filesQuery.isPending && !recordFiles) {
    return <SkeletonStatus label={t("record.loading")} className="space-y-3 p-4">
      <Skeleton className="h-9" /><Skeleton className="h-12" /><Skeleton className="h-12" />
    </SkeletonStatus>;
  }
  if (filesQuery.error && !recordFiles) return <ErrorBanner description={filesQuery.error.message} />;
  if (!recordFiles?.available) return <EmptyState icon="file" title={t("record.unavailable")} />;

  const attachments = recordFiles.attachments;
  const selected = attachments.find((edge) => edge.file.id === selectedId);
  return <UploadDropTarget className="flex h-full min-h-0 flex-col" disabled={!canUpload}
    overlay={t("upload.dropOverlay")} onFiles={startUpload}>
    {drivesQuery.error && recordFiles.can_upload ? <ErrorBanner description={drivesQuery.error.message} /> : null}
    {recordFiles.can_upload ? <div className="flex gap-2 border-b border-border-subtle p-3">
      {drives.length > 1 ? <Select aria-label={t("drive.label")} className="min-w-0 flex-1"
        value={selectedDriveId} options={drives.map((drive) => ({ value: drive.id, label: drive.name }))}
        onValueChange={setDriveId} /> : null}
      <Button type="button" size="sm" variant="secondary" disabled={!canUpload}
        onClick={() => inputRef.current?.click()}>{t("upload.button")}</Button>
      <input ref={inputRef} type="file" multiple className="hidden" onChange={(event) => {
        startUpload(event.currentTarget.files);
        event.currentTarget.value = "";
      }} />
    </div> : null}
    {uploads.tasks.length ? <StorageUploadTasks uploads={uploads} t={t} /> : null}
    {drivesQuery.isPending && recordFiles.can_upload ? <SkeletonStatus label={t("record.loadingDrives")} className="p-3"><Skeleton className="h-8" /></SkeletonStatus> : null}
    {attachments.length ? <ul className="min-h-0 overflow-auto border-b border-border-subtle">
      {attachments.map((edge) => <li key={edge.id}>
        <button type="button" className="w-full px-3 py-2 text-left text-sm hover:bg-inset"
          aria-pressed={selectedId === edge.file.id} onClick={() => setSelectedId(edge.file.id)}>
          {edge.label || edge.file.title || edge.file.filename}
        </button>
      </li>)}
    </ul> : <EmptyState icon="file" title={t("record.empty")} />}
    {selected ? <div className="min-h-48 flex-1"><FileRecordPreview id={selected.file.id} /></div> : null}
  </UploadDropTarget>;
}
