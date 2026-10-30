import { useAuthoredQuery } from "@angee/refine";
import { EmptyState, ErrorBanner, PreviewPane, Skeleton, SkeletonStatus, routeSearchParam } from "@angee/ui";
import type { ReactElement } from "react";

import { StorageFileById, type StorageFile } from "../data/documents";
import { useStorageT } from "../i18n";

/** Portable detail key; its value binds the requested page to one exact File. */
export const FILE_PREVIEW_PAGE_PARAM = "previewPage";

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
  if (!file) return query.isFetching ? <SkeletonStatus label={t("loadingFile")} className="space-y-3 p-3">
    <Skeleton className="h-6 w-2/3" /><Skeleton className="h-40" />
  </SkeletonStatus> : <EmptyState icon="file" title={t("file.notFoundTitle")} />;
  // The record page's header already names the file; the tab is the preview alone.
  return <FilePreview file={file} page={page} />;
}
