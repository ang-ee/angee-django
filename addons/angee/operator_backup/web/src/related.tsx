import { ResourceQuery, useModelMetadata, type Row } from "@angee/metadata";
import { useAngeeAggregate } from "@angee/refine";
import { AbsoluteTime, ListView, PaneRow, useAggregateOperation, useResourceRecordHrefLookup, type ChatterViewContext, type ContainerChild, type RelatedGroupContent } from "@angee/ui";
import { enBackupMessages, useBackupT } from "./i18n";
import { BACKUP_MODEL } from "./resources";

function planId(context: ChatterViewContext): string {
  return context.view.kind === "record" ? context.view.sqid ?? "" : "";
}
function useCompletedBackupCount(context: ChatterViewContext) {
  const resource = useModelMetadata(BACKUP_MODEL)?.resource ?? null;
  const operation = useAggregateOperation(resource);
  const id = planId(context);
  const query = useAngeeAggregate(operation.target, {
    document: operation.document, enabled: Boolean(id && resource),
    where: resource ? ResourceQuery.from(resource).toWhere({ plan: { exact: id } }) : undefined,
  });
  return query.aggregate?.count;
}
export const completedBackupsGroup: ContainerChild<RelatedGroupContent> = {
  sequence: 50,
  content: {
    label: enBackupMessages.captures,
    useCount: useCompletedBackupCount,
    render: (context, label) => <>{label()}<li><CompletedBackups key={context.view.sqid} context={context} /></li></>,
  },
};
interface CompletedBackupRow extends Row { id: string; display_name: string; created_at: string }
function CompletedBackups({ context }: { context: ChatterViewContext }) {
  const t = useBackupT();
  const href = useResourceRecordHrefLookup();
  return <ListView<CompletedBackupRow> resource={BACKUP_MODEL} scope="local" presentation="embedded" selectable={false}
    baseFilter={{ plan: { exact: planId(context) } }} order={{ created_at: "DESC" }} pageSize={10}
    columns={[{ field: "display_name", header: t("backup") }, { field: "created_at", header: t("created") }]}
    availableViews={["list"]} chrome={{ heading: false }} emptyContent={t("backupEmpty")}
    renderPaneRow={(backup) => <PaneRow icon="archive" title={backup.display_name} href={href(BACKUP_MODEL, backup.id)} meta={<AbsoluteTime value={backup.created_at} display="date" />} />} />;
}
