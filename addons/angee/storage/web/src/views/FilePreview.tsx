import { useAuthoredQuery } from "@angee/refine";
import { EmptyState, ErrorBanner, LoadingPanel, PreviewPane, routeSearchParam, type RecordPeekReference } from "@angee/ui";
import type { ReactElement } from "react";

import { StorageFileById, type StorageFile } from "../data/documents";
import { useStorageT } from "../i18n";

/** Portable detail key; its value binds the requested page to one exact File. */
export const FILE_PREVIEW_PAGE_PARAM = "previewPage";

export function filePreviewReference(id: string, oneBasedPage?: number | null): RecordPeekReference {
  const page = validPreviewPage(oneBasedPage);
  return {
    model: "storage.File",
    id,
    tab: "preview",
    ...(page === null ? {} : {
      page,
      search: { [FILE_PREVIEW_PAGE_PARAM]: `${id}:${page}` },
    }),
  };
}

export function filePreviewPageFromSearch(
  search: Readonly<Record<string, unknown>>,
  fileId: string,
): number | null {
  const raw = routeSearchParam(search, FILE_PREVIEW_PAGE_PARAM);
  if (typeof raw !== "string" || !raw.startsWith(`${fileId}:`)) return null;
  return validPreviewPage(Number(raw.slice(fileId.length + 1)));
}

function validPreviewPage(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isInteger(value) && value > 0 ? value : null;
}

/** Shared Storage adapter: download URL and access come from the File resource. */
export function FilePreview({ file, page }: { file: StorageFile; page?: number | null }): ReactElement {
  const t = useStorageT();
  const fallback = <EmptyState icon="file" title={file.title || file.filename} description={t("preview.unsupported")} />;
  if (!file.url) return fallback;
  return <PreviewPane file={{ url: file.url, name: file.filename, mime: file.mime_type?.mime_type, size: file.size_bytes }} page={page}
    fallback={fallback} />;
}

export function FileRecordPreview({ id, page }: { id: string; page?: number | null }): ReactElement {
  const t = useStorageT();
  const query = useAuthoredQuery(StorageFileById, { id }, { models: ["storage.File"] });
  const file = query.data?.files_by_pk;
  if (query.error) return <ErrorBanner title={t("preview.loadError")} description={query.error.message} />;
  if (!file) return query.isFetching ? <LoadingPanel message={t("loadingFile")} /> : <EmptyState icon="file" title={t("file.notFoundTitle")} />;
  return <div className="flex h-full min-h-0 flex-col">
    <div className="shrink-0 border-b border-border-subtle px-3 py-2 text-xs text-fg-muted">
      <p className="truncate" title={file.filename}>{file.title || file.filename}</p>
    </div>
    <div className="min-h-0 flex-1"><FilePreview file={file} page={page} /></div>
  </div>;
}
