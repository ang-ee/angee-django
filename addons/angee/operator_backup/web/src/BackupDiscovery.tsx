import { ResourceQuery } from "@angee/metadata";
import { Button, ErrorBanner, InlineEmpty, LoadingPanel, MetaGrid, RelativeTime, RowsListView, SelectionBarAction, StatusBadge, formatSize, optionToken, type ListColumn } from "@angee/ui";
import { type BackupPreview } from "./documents";
import { useBackupT } from "./i18n";
import { applyInventorySelection, matchingPreviewSelection, type BackupSelection } from "./selection";

type InventoryRow = NonNullable<BackupPreview>["resources"][number] & { id: string };
const query = ResourceQuery.forRows({ fields: {
  group: { scalar: "String" }, name: { scalar: "String" }, status: { scalar: "String" }, reason: { scalar: "String" }, estimated_bytes: { scalar: "BigInt" },
} });

export function BackupDiscoveryView({ preview, pending, error, selection, readOnly, onChange, onRetry }: {
  preview?: BackupPreview; pending: boolean; error?: Error | null;
  selection: BackupSelection; readOnly?: boolean; onChange?: (selection: BackupSelection) => void; onRetry: () => void;
}) {
  const t = useBackupT();
  const stale = Boolean(preview) && !matchingPreviewSelection(preview?.selection, selection);
  const rows: InventoryRow[] = (preview?.resources ?? []).map((resource) => ({ ...resource, id: JSON.stringify([resource.group, resource.name]) }));
  // GraphQL enum members stay on the read model; selection JSON uses the operator tokens.
  const selectionRows = rows.map(({ id, group, name }) => ({ id, group: optionToken(group), name }));
  const columns: readonly ListColumn<InventoryRow>[] = [
    { field: "group", header: t("group"), render: (row) => t(optionToken(row.group)) },
    { field: "name", header: t("resource") },
    { field: "status", header: t("status"), render: (row) => <StatusBadge value={row.status} /> },
    { field: "reason", header: t("reason") },
    { field: "estimated_bytes", header: t("sourceBytes"), render: (row) => row.estimated_bytes == null ? t("unknown") : formatSize(row.estimated_bytes) },
  ];
  return <div className="space-y-3">
    <ErrorBanner description={error?.message ?? null} actions={
      <Button type="button" variant="secondary" size="sm" disabled={pending} pending={pending} onClick={onRetry}>{t("retryDiscovery")}</Button>
    } />
    {stale && !error ? <p role="status" className="text-13 text-fg-muted">{t("previewStale")}</p> : null}
    {preview ? <>
      <MetaGrid rows={[[t("discoveredAt"), <RelativeTime value={preview.discovered_at} />], [t(preview.has_unknown_sizes ? "knownSourceBytes" : "sourceBytes"), formatSize(preview.estimated_bytes)]]} />
      {preview.has_unknown_sizes ? <p className="text-13 text-fg-muted">{t("unknownSizes")}</p> : null}
      <p className="text-13 text-fg-muted">{t("compressionHint")}</p>
      {rows.length > 0 || !error ? <RowsListView rows={rows} query={query} columns={columns} fetching={pending} presentation="embedded" scope="local" selectable={!readOnly}
        emptyContent={t("previewEmpty")} bulkActions={!readOnly && onChange ? (ids, clear) => <>
          <SelectionBarAction disabled={pending} onClick={() => { onChange(applyInventorySelection(selection, selectionRows.filter((row) => ids.has(row.id)), "include")); clear(); }}>{t("includeChecked")}</SelectionBarAction>
          <SelectionBarAction disabled={pending} onClick={() => { onChange(applyInventorySelection(selection, selectionRows.filter((row) => ids.has(row.id)), "exclude")); clear(); }}>{t("excludeChecked")}</SelectionBarAction>
        </> : undefined} /> : null}
    </> : pending ? <LoadingPanel message={t("discovering")} /> : !error ? <InlineEmpty label={t("previewEmpty")} /> : null}
  </div>;
}
