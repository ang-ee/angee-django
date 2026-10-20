import { useAuthoredQuery } from "@angee/refine";
import {
  ErrorBanner, FilePickerButton, Glyph, InlineEmpty, PaneLabelPopover, PaneRow, PaneRows, Select, Skeleton, SkeletonStatus, UploadDropTarget,
  formatSize, useResourceRecordHrefLookup, useRuntimeViewAs, type ChatterViewContext, type ContainerChild, type RelatedGroupContent,
  type RelatedGroupLabel,
} from "@angee/ui";
import { useMemo, useState, type ReactElement } from "react";

import { FILE_ATTACHMENT_MODEL, STORAGE_CATALOGUE_LIMIT, StorageDrives, StorageRecordFiles } from "./data/documents";
import { useStorageUpload } from "./data/use-upload";
import { useStorageT } from "./i18n";
import { StorageUploadTasks } from "./views/StorageUploadTasks";

const FILE_MODEL = "storage.File";
const ATTACHMENT_MODELS = [FILE_ATTACHMENT_MODEL] as const;

/** The count, presence probe and rows observe the same actor-scoped read. */
function useRecordFilesRead(context: ChatterViewContext) {
  const modelLabel = context.route?.modelLabel ?? "";
  const recordId = context.view.kind === "record" ? context.view.sqid ?? "" : "";
  const variables = useMemo(() => ({ modelLabel, recordId }), [modelLabel, recordId]);
  return useAuthoredQuery(StorageRecordFiles, variables, {
    enabled: Boolean(modelLabel && recordId), models: ATTACHMENT_MODELS,
  });
}

function useRecordFilesVerb(context: ChatterViewContext): boolean {
  const preview = useRuntimeViewAs();
  const query = useRecordFilesRead(context);
  return Boolean(query.data?.record_files.can_upload && !preview.viewAs && !preview.pending);
}

/** The Files group of the Related tab, where the record type carries attachments. */
export const recordFilesGroup: ContainerChild<RelatedGroupContent> = {
  sequence: 10,
  content: {
    label: "Files",
    edge: { model: FILE_ATTACHMENT_MODEL },
    useCount: (context) => useRecordFilesRead(context).data?.record_files.attachments.length,
    useVerb: useRecordFilesVerb,
    render: (context, label) => <RecordFilesGroup key={context.view.sqid} context={context} label={label} />,
  },
};

/** The label picker, drop target and progress share one upload lifetime. */
function RecordFilesGroup({ context, label }: { context: ChatterViewContext; label: RelatedGroupLabel }): ReactElement {
  const t = useStorageT();
  const recordHref = useResourceRecordHrefLookup();
  const filesQuery = useRecordFilesRead(context);
  const offersUpload = useRecordFilesVerb(context);
  const drivesQuery = useAuthoredQuery(StorageDrives, { limit: STORAGE_CATALOGUE_LIMIT, offset: 0 }, {
    enabled: offersUpload, models: ["storage.Drive"],
  });
  const uploads = useStorageUpload();
  const [driveId, setDriveId] = useState("");
  const recordFiles = filesQuery.data?.record_files;
  const drives = drivesQuery.data?.drives ?? [];
  const selectedDriveId = drives.some((drive) => drive.id === driveId) ? driveId : drives[0]?.id ?? "";
  const canUpload = offersUpload && Boolean(selectedDriveId);

  function startUpload(files: readonly File[]): void {
    if (!files.length || !canUpload) return;
    uploads.upload(Array.from(files), {
      driveId: selectedDriveId,
      visibility: "RECORD",
      record: { model_label: context.route?.modelLabel ?? "", record_id: context.view.sqid ?? "" },
    });
  }

  return <>
    {label(<PaneLabelPopover icon="plus" label={t("upload.button")}>
      {drivesQuery.isPending ? <SkeletonStatus label={t("record.loadingDrives")}><Skeleton className="h-7" /></SkeletonStatus> : null}
      {drivesQuery.error ? <ErrorBanner description={drivesQuery.error.message} /> : null}
      {drivesQuery.data && !drives.length ? <InlineEmpty icon="drive" label={t("drives.emptyDescription")} /> : null}
      {drives.length > 1 ? <Select aria-label={t("drive.label")} value={selectedDriveId}
        options={drives.map((drive) => ({ value: drive.id, label: drive.name }))}
        placeholder={t("drive.placeholder")} onValueChange={setDriveId} /> : null}
      <FilePickerButton size="sm" variant="secondary" disabled={!canUpload} label={t("upload.chooseFiles")} onFiles={startUpload}>
        <Glyph name="upload" />{t("upload.chooseFiles")}
      </FilePickerButton>
    </PaneLabelPopover>)}
    <li>
      {!recordFiles ? filesQuery.error ? <ErrorBanner description={filesQuery.error.message} /> :
        <SkeletonStatus label={t("record.loading")} className="space-y-2 p-2"><Skeleton className="h-8" /><Skeleton className="h-8" /></SkeletonStatus> :
        <UploadDropTarget className="flex min-h-0 flex-col" disabled={!canUpload} overlay={t("upload.dropOverlay")} onFiles={startUpload}>
          {recordFiles.attachments.length ? <PaneRows>
            {recordFiles.attachments.map((edge) => <li key={edge.id}>
              <PaneRow icon="file" title={edge.label || edge.file.title || edge.file.filename}
                meta={formatSize(edge.file.size_bytes)} sub={edge.file.created_by_label || edge.file.filename}
                href={recordHref(FILE_MODEL, edge.file.id)} />
            </li>)}
          </PaneRows> : <InlineEmpty icon="file" label={t("record.empty")} />}
        </UploadDropTarget>}
      {uploads.tasks.length ? <StorageUploadTasks uploads={uploads} t={t} /> : null}
    </li>
  </>;
}
