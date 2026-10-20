import type { ReactElement } from "react";
import { useAuthoredQuery } from "@angee/refine";
import { Button, buttonVariants, ControlBand, ControlBandProvider, EmptyState, ErrorBanner,
  formatSize, Glyph, LoadingPanel, RecordPager, SurfaceHeader, type RecordNavigation } from "@angee/ui";

import { StorageFileById } from "../data/documents";
import type { FileActions } from "../data/use-file-actions";
import { useStorageT } from "../i18n";
import { FileDetail } from "./FileDetail";
import { FilePreview } from "./FilePreview";

/** The open file's control band, preview and inline Details form. */
export function FilePreviewFrame({
  id,
  page,
  fileActions,
  closeDetail,
  fileNavigation,
}: {
  id: string;
  page?: number | null;
  fileActions: FileActions;
  closeDetail: () => void;
  fileNavigation: RecordNavigation | null;
}): ReactElement {
  const t = useStorageT();
  const query = useAuthoredQuery(StorageFileById, { id }, { models: ["storage.File"] });
  const file = query.data?.files_by_pk ?? null;
  const { isFetching: fetching, error } = query;
  // The file's verbs (download, trash/restore) live in the shell control band,
  // beside the preview; this frame titles the file, renders its content and,
  // under it, its metadata form: the subject owns its details, the aside what
  // relates to it.
  return (
    <>
      <ControlBand>
        {file && !file.is_trashed && file.url ? (
          <a
            className={buttonVariants({ variant: "secondary", size: "sm" })}
            href={file.url}
            download={file.filename}
          >
            <Glyph name="download" />
            {t("file.download")}
          </a>
        ) : null}
        {file?.is_trashed ? (
          <Button
            type="button"
            size="sm"
            variant="secondary"
            loading={fileActions.busy}
            onClick={() => void fileActions.restore(file.id)}
          >
            <Glyph name="restore" />
            {t("file.restore")}
          </Button>
        ) : (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            loading={fileActions.busy}
            disabled={!file}
            onClick={() => {
              if (file) void fileActions.trash(file.id).then(closeDetail);
            }}
          >
            <Glyph name="trash" />
            {t("file.trash")}
          </Button>
        )}
        {fileNavigation ? (
          <div className="ml-auto">
            <RecordPager navigation={fileNavigation} />
          </div>
        ) : null}
      </ControlBand>
      <div className="flex h-full min-h-0 flex-col bg-canvas">
        <SurfaceHeader
          density="compact"
          headingLevel={2}
          icon={file?.mime_type?.icon_key || "file"}
          title={file
            ? file.title || file.filename
            : fetching
              ? t("loadingFile")
              : error ? t("preview.loadError") : t("file.notFoundTitle")}
          subtitle={file
            ? t("file.subtitle", {
                type:
                  file.mime_type?.label ||
                  file.mime_type?.mime_type ||
                  t("file.unknownType"),
                size: formatSize(file.size_bytes),
              })
            : undefined}
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
          {/* The metadata form's own save row stays inline in its section; the page's
              control band keeps the file's lifecycle verbs and the pager. */}
          <section aria-label={t("file.details")} className="mt-4 rounded-8 border border-border-subtle bg-sheet p-4">
            <ControlBandProvider host={undefined}>
              <FileDetail id={id} filename={file?.filename} onChanged={query.refetch} />
            </ControlBandProvider>
          </section>
        </div>
      </div>
    </>
  );
}
