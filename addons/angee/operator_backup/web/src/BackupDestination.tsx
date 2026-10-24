import { useAuthoredQuery } from "@angee/refine";
import { ErrorBanner, InlineEmpty, RecordReference, Spinner } from "@angee/ui";
import { BackupDestinationDocument } from "./documents";
import { useBackupT } from "./i18n";

/** Native Query shares this configured destination read between every backup plan. */
export function BackupDestination() {
  const t = useBackupT();
  const query = useAuthoredQuery(BackupDestinationDocument, undefined, { dataProviderName: "console", models: ["storage.Drive"], errorNotification: false });
  if (query.error) return <ErrorBanner description={query.error.message} />;
  if (query.isPending) return <Spinner label={t("loadingDestination")} />;
  const id = query.data?.backup_destination_drive;
  return id ? <RecordReference model="storage.Drive" id={id} /> : <InlineEmpty label={t("destinationUnavailable")} />;
}
