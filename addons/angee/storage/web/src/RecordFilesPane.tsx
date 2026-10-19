import { useAuthoredQuery } from "@angee/refine";
import {
  Button, EmptyState, ErrorBanner, PaneRow, PaneRows, Select, Skeleton, SkeletonStatus, UploadDropTarget, formatSize,
  useResourceRecordHrefLookup, useRuntimeViewAs, type ChatterViewContext, type ContainerChild, type RelatedGroupContent,
} from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";
import { useMemo, useRef, useState, type ReactElement } from "react";

import { STORAGE_CATALOGUE_LIMIT, StorageDrives, StorageRecordFiles } from "./data/documents";
import { useStorageUpload } from "./data/use-upload";
import { useStorageT } from "./i18n";
import { StorageUploadTasks } from "./views/StorageUploadTasks";

/** The edge a record carries its files through; resource metadata lists it where a type can. */
export const FILE_ATTACHMENT_MODEL = "storage.FileAttachment";
const FILE_MODEL = "storage.File";
const ATTACHMENT_MODELS = [FILE_ATTACHMENT_MODEL] as const;

/** The Related group's record, as the pane expects it. */
export function recordFilesTarget(context: ChatterViewContext): RecordFilesTarget {
  return {
    modelLabel: context.route?.modelLabel ?? "",
    recordId: context.view.kind === "record" ? context.view.sqid ?? "" : "",
  };
}

/** The group's count and verb use the same actor-scoped read as its rows. */
function useRecordFilesRead(context: ChatterViewContext) {
  const { modelLabel, recordId } = recordFilesTarget(context);
  const variables = useMemo(() => ({ modelLabel, recordId }), [modelLabel, recordId]);
  return useAuthoredQuery(StorageRecordFiles, variables, {
    enabled: Boolean(modelLabel && recordId), models: ATTACHMENT_MODELS,
  });
}

export function useRecordFilesCount(context: ChatterViewContext): number | undefined {
  return useRecordFilesRead(context).data?.record_files.attachments.length;
}

/** Upload is offered where the record's type has an attachment arm and the actor may write it. */
export function useRecordFilesVerb(context: ChatterViewContext): boolean {
  return useRecordFilesRead(context).data?.record_files.can_upload ?? false;
}

/** The record whose attachments a pane lists. */
export interface RecordFilesTarget { modelLabel: string; recordId: string }

/** The Files group of the Related tab: on every record whose type carries file attachments. */
export const recordFilesGroup: ContainerChild<RelatedGroupContent> = {
  sequence: 10,
  content: {
    label: "Files",
    when: (context) => context.route?.recordEdges?.includes(FILE_ATTACHMENT_MODEL) ?? false,
    useCount: useRecordFilesCount,
    useVerb: useRecordFilesVerb,
    render: (context) => <RecordFilesPane target={recordFilesTarget(context)} />,
  },
};

/** Record-scoped file rows, each a link to its file page, with the existing upload
 *  protocol as the group's one verb; the Related tab and a page section both hand
 *  it the record they show. */
export function RecordFilesPane({ target }: { target: RecordFilesTarget }): ReactElement {
  const t = useStorageT();
  const preview = useRuntimeViewAs();
  const recordHref = useResourceRecordHrefLookup();
  const navigate = useNavigate();
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
    return <SkeletonStatus label={t("record.loading")} className="space-y-2 p-2">
      <Skeleton className="h-8" /><Skeleton className="h-8" />
    </SkeletonStatus>;
  }

  const attachments = recordFiles.attachments;
  return <UploadDropTarget className="flex min-h-0 flex-col" disabled={!canUpload}
    overlay={t("upload.dropOverlay")} onFiles={startUpload}>
    {drivesQuery.error && recordFiles.can_upload ? <ErrorBanner description={drivesQuery.error.message} /> : null}
    {attachments.length ? <PaneRows>
      {attachments.map((edge) => {
        const href = recordHref(FILE_MODEL, edge.file.id);
        return <li key={edge.id}>
          <PaneRow
            icon="file"
            title={edge.label || edge.file.title || edge.file.filename}
            meta={formatSize(edge.file.size_bytes)}
            sub={edge.file.created_by_label || edge.file.filename}
            href={href}
            onOpen={href ? () => void navigate({ href } as never) : undefined}
          />
        </li>;
      })}
    </PaneRows> : <EmptyState icon="file" title={t("record.empty")} className="min-h-24 p-3" />}
    {uploads.tasks.length ? <StorageUploadTasks uploads={uploads} t={t} /> : null}
    {recordFiles.can_upload ? <div className="flex items-center gap-2 px-1 py-1">
      {drivesQuery.isPending ? <SkeletonStatus label={t("record.loadingDrives")} className="flex-1"><Skeleton className="h-7" /></SkeletonStatus> : null}
      {drives.length > 1 ? <Select aria-label={t("drive.label")} className="min-w-0 flex-1"
        value={selectedDriveId} options={drives.map((drive) => ({ value: drive.id, label: drive.name }))}
        onValueChange={setDriveId} /> : null}
      <Button type="button" size="sm" variant="ghost" disabled={!canUpload}
        onClick={() => inputRef.current?.click()}>{t("upload.button")}</Button>
      <input ref={inputRef} type="file" multiple className="hidden" onChange={(event) => {
        startUpload(event.currentTarget.files);
        event.currentTarget.value = "";
      }} />
    </div> : null}
  </UploadDropTarget>;
}
