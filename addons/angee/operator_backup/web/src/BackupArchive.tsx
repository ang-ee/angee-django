import type { DocumentType } from "@angee/gql/console";
import { useAuthoredQuery } from "@angee/refine";
import { Button, ErrorBanner, Glyph, InlineEmpty, LoadingPanel, MetaGrid, RecordReference, formatSize } from "@angee/ui";
import { BackupArchiveFileDocument } from "./documents";
import { useBackupT } from "./i18n";

/** A File remains the only owner of authorization, download URL, size, and checksum. */
export function BackupArchive({ id }: { id?: string }) {
  const query = useAuthoredQuery(BackupArchiveFileDocument, { id: id ?? "" }, { dataProviderName: "console", enabled: Boolean(id), models: ["storage.File"] });
  return <BackupArchiveView file={query.data?.files_by_pk} pending={query.isFetching} error={query.error} />;
}
export function BackupArchiveView({ file, pending, error }: {
  file?: DocumentType<typeof BackupArchiveFileDocument>["files_by_pk"];
  pending: boolean; error?: Error | null;
}) {
  const t = useBackupT();
  const available = file && !file.is_trashed && file.url;
  return <div className="space-y-3">
    <ErrorBanner description={error?.message ?? null} />
    {available ? <Button nativeButton={false} role="link" render={<a href={file.url ?? undefined} download={file.filename} />}><Glyph name="download" />{t("download")}</Button>
      : <Button disabled pending={pending}><Glyph name="download" />{t("download")}</Button>}
    {file ? <>
      <MetaGrid rows={[
        [t("archive"), <RecordReference model="storage.File" id={file.id} label={file.filename} />],
        [t("size"), formatSize(file.size_bytes)],
        [t("checksum"), file.content_hash],
      ]} />
    </> : <>{pending ? <LoadingPanel message={t("loadingArchive")} /> : !error ? <InlineEmpty label={t("downloadUnavailable")} /> : null}</>}
  </div>;
}
