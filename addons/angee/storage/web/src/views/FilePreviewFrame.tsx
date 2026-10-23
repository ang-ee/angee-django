import { useMemo, type ReactElement, type ReactNode } from "react";
import { useAuthoredQuery } from "@angee/refine";
import { useModelMetadata, type Row } from "@angee/metadata";
import {
  Button, Card, CardContent, CommandBand, ControlBandProvider, EmptyState, ErrorBanner, formatDate, formatSize,
  Glyph, LoadingPanel, RecordHeader, useConfirm, type RecordChromeContext, type RecordNavigation,
} from "@angee/ui";

import { StorageFileById } from "../data/documents";
import { useFileTypeLabel } from "../data/file-rows";
import type { FileActions } from "../data/use-file-actions";
import { useStorageT } from "../i18n";
import { FileDetail } from "./FileDetail";
import { FilePreview } from "./FilePreview";

const FILE_MODEL = "storage.File";

/** The open file as the subject: command band, record header, preview, then its Details form. */
export function FilePreviewFrame({
  id,
  page,
  crumbs,
  fileActions,
  closeDetail,
  fileNavigation,
}: {
  id: string;
  page?: number | null;
  /** The eyebrow: kind and container path ("File · AP drive / 2026 / October"). */
  crumbs?: ReactNode;
  fileActions: FileActions;
  closeDetail: () => void;
  fileNavigation: RecordNavigation | null;
}): ReactElement {
  const t = useStorageT();
  const confirm = useConfirm();
  const metadata = useModelMetadata(FILE_MODEL);
  const query = useAuthoredQuery(StorageFileById, { id }, { models: [FILE_MODEL] });
  const file = query.data?.files_by_pk ?? null;
  const { isFetching: fetching, error } = query;
  const chrome = useMemo<RecordChromeContext | null>(() => file ? {
    resource: FILE_MODEL,
    dataProviderName: metadata?.resource.schemaName,
    canonicalResource: FILE_MODEL,
    recordId: file.id,
    record: file as unknown as Row,
    formReadOnly: false,
  } : null, [file, metadata?.resource.schemaName]);
  const fileTypeLabel = useFileTypeLabel();
  const type = file ? fileTypeLabel(file) : "";
  const facts = file
    ? t(file.created_by_label ? "file.facts" : "file.factsAnonymous", {
        type, size: formatSize(file.size_bytes), date: formatDate(file.updated_at), by: file.created_by_label ?? "",
      })
    : undefined;
  return (
    <>
      <CommandBand
        // Lifecycle verbs stay mounted across record loads (disabled meanwhile), so the
        // band never flickers when the pager moves to the next file.
        verbs={<>
          {file && !file.is_trashed && file.url ? (
            <Button size="sm" variant="primary" nativeButton={false} role="link" render={<a href={file.url} download={file.filename} />}>
              <Glyph name="download" />
              {t("file.download")}
            </Button>
          ) : (
            <Button type="button" size="sm" variant="primary" disabled>
              <Glyph name="download" />
              {t("file.download")}
            </Button>
          )}
          {file?.is_trashed ? (
            <Button type="button" size="sm" variant="secondary" loading={fileActions.busy}
              onClick={() => void fileActions.restore(file.id)}>
              <Glyph name="restore" />
              {t("file.restore")}
            </Button>
          ) : (
            <Button type="button" size="sm" variant="danger" loading={fileActions.busy} disabled={!file}
              onClick={async () => {
                if (!file) return;
                const ok = await confirm({
                  title: t("file.trashTitle", { name: file.title || file.filename }),
                  body: t("file.trashBody"),
                  confirm: t("file.trash"),
                  danger: true,
                });
                if (ok) void fileActions.trash(file.id).then(closeDetail);
              }}>
              <Glyph name="trash" />
              {t("file.trash")}
            </Button>
          )}
        </>}
        chrome={chrome}
        navigation={fileNavigation}
      />
      <div className="flex h-full min-h-0 flex-col bg-canvas">
        <RecordHeader
          headingLevel={1}
          crumbs={crumbs}
          title={file
            ? file.title || file.filename
            : fetching
              ? t("loadingFile")
              : error ? t("preview.loadError") : t("file.notFoundTitle")}
          meta={facts}
          status={file?.is_trashed ? { label: t("file.inTrash"), tone: "warning" } : undefined}
        />
        <div className="min-h-0 flex-1 overflow-auto p-3">
          <div className="h-[65vh] min-h-80">
            {file ? (
              <FilePreview file={file} page={page} />
            ) : fetching ? (
              <LoadingPanel message={t("loadingFile")} />
            ) : error ? (
              <ErrorBanner title={t("preview.loadError")} description={error.message} />
            ) : (
              <EmptyState
                fill
                icon="file"
                title={t("file.notFoundTitle")}
                description={t("file.notFoundDescription")}
              />
            )}
          </div>
          {/* The metadata form's own save row stays inline in its card; the band
              keeps the file's lifecycle verbs and the pager. */}
          <Card render={<section aria-label={t("file.details")} />} className="mt-4">
            <CardContent>
              <ControlBandProvider host={undefined}>
                <FileDetail id={id} filename={file?.filename} onChanged={query.refetch} />
              </ControlBandProvider>
            </CardContent>
          </Card>
        </div>
      </div>
    </>
  );
}
