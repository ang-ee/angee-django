import { useAuthoredQuery } from "@angee/refine";
import { Button, EmptyState, ErrorBanner, Select, Skeleton, SkeletonStatus, UploadDropTarget, useRuntimeViewAs, type ChatterViewContext } from "@angee/ui";
import { useMemo, useRef, useState, type ReactElement } from "react";

import { STORAGE_CATALOGUE_LIMIT, StorageDrives, StorageRecordFiles } from "./data/documents";
import { useStorageUpload } from "./data/use-upload";
import { useStorageT } from "./i18n";
import { FileRecordPreview } from "./views/FilePreview";
import { StorageUploadTasks } from "./views/StorageUploadTasks";

/** The edge a record carries its files through; resource metadata lists it where a type can. */
export const FILE_ATTACHMENT_MODEL = "storage.FileAttachment";
const ATTACHMENT_MODELS = [FILE_ATTACHMENT_MODEL] as const;

/** The chatter tab's record, as the pane expects it. */
export function recordFilesTarget(context: ChatterViewContext): RecordFilesTarget {
  return {
    modelLabel: context.route?.modelLabel ?? "",
    recordId: context.view.kind === "record" ? context.view.sqid ?? "" : "",
  };
}

/** Count uses the same actor-scoped read as the panel. */
export function useRecordFilesCount(context: ChatterViewContext): number | undefined {
  const { modelLabel, recordId } = recordFilesTarget(context);
  const variables = useMemo(() => ({ modelLabel, recordId }), [modelLabel, recordId]);
  const query = useAuthoredQuery(StorageRecordFiles, variables, {
    enabled: Boolean(modelLabel && recordId), models: ATTACHMENT_MODELS,
  });
  return query.data?.record_files.attachments.length;
}

/** The record whose attachments a pane lists. */
export interface RecordFilesTarget { modelLabel: string; recordId: string }

/** Record-scoped file list, inline preview, and the existing upload protocol; the
 *  chatter tab and a page section both hand it the record they show. */
export function RecordFilesPane({ target }: { target: RecordFilesTarget }): ReactElement {
  const t = useStorageT();
  const preview = useRuntimeViewAs();
  const { modelLabel, recordId } = target;
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

  if (!recordFiles) {
    if (filesQuery.error) return <ErrorBanner description={filesQuery.error.message} />;
    return <SkeletonStatus label={t("record.loading")} className="space-y-3 p-4">
      <Skeleton className="h-9" /><Skeleton className="h-12" /><Skeleton className="h-12" />
    </SkeletonStatus>;
  }

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
