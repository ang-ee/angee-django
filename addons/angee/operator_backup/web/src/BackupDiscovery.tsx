import * as v from "valibot";
import { ResourceQuery } from "@angee/metadata";
import { useAuthoredQuery } from "@angee/refine";
import { Button, ErrorBanner, InlineEmpty, MetaGrid, RelativeTime, RowsListView, SelectionBarAction, StatusBadge, formatSize, optionToken, type ListColumn } from "@angee/ui";
import { BackupPlanPreviewDocument, type BackupPreview } from "./documents";
import { PLAN_MODEL } from "./resources";
import { useBackupT } from "./i18n";
import { applyInventorySelection, BackupSelectionSchema, type BackupSelection } from "./selection";

type InventoryRow = NonNullable<BackupPreview>["resources"][number] & { id: string };
const query = ResourceQuery.forRows({ fields: {
  group: { scalar: "String" }, name: { scalar: "String" }, status: { scalar: "String" }, reason: { scalar: "String" }, estimated_bytes: { scalar: "BigInt" },
} });

/** A fresh operator read is explicit; native Query retains the latest snapshot for this saved plan. */
export function BackupDiscovery({ id, selection, readOnly, disabled, discoveryBlocked, onChange }: {
  id?: string; discoveryBlocked?: boolean; selection: BackupSelection; readOnly?: boolean; disabled?: boolean; onChange?: (selection: BackupSelection) => void;
}) {
  const result = useAuthoredQuery(BackupPlanPreviewDocument, { id: id ?? "" }, {
    dataProviderName: "console", enabled: false, models: [PLAN_MODEL], errorNotification: false,
  });
  return <BackupDiscoveryView preview={result.data?.preview_backup_plan} pending={result.isFetching} error={result.error}
    canDiscover={Boolean(id) && !disabled && !discoveryBlocked} discover={() => { void result.refetch(); }}
    selection={selection} readOnly={readOnly || disabled} onChange={onChange} />;
}

export function BackupDiscoveryView({ preview, pending, error, canDiscover, discover, selection, readOnly, onChange }: {
  preview?: BackupPreview; pending: boolean; error?: Error | null; canDiscover: boolean; discover: () => void;
  selection: BackupSelection; readOnly?: boolean; onChange?: (selection: BackupSelection) => void;
}) {
  const t = useBackupT();
  const snapshot = v.safeParse(BackupSelectionSchema, preview?.selection);
  const stale = Boolean(preview) && (!snapshot.success || JSON.stringify(snapshot.output) !== JSON.stringify(selection));
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
    <Button type="button" onClick={discover} disabled={!canDiscover || pending} pending={pending} loadingText={t("discovering")}>{t("discover")}</Button>
    <p className="text-13 text-fg-muted">{t("previewHint")}</p>
    <ErrorBanner description={error?.message ?? null} />
    {stale ? <p role="status" className="text-13 text-fg-muted">{t("previewStale")}</p> : null}
    {preview ? <>
      <MetaGrid rows={[[t("discoveredAt"), <RelativeTime value={preview.discovered_at} />], [t("sourceBytes"), formatSize(preview.estimated_bytes)]]} />
      {preview.has_unknown_sizes ? <p className="text-13 text-fg-muted">{t("unknownSizes")}</p> : null}
      <p className="text-13 text-fg-muted">{t("compressionHint")}</p>
      <RowsListView rows={rows} query={query} columns={columns} fetching={pending} presentation="embedded" scope="local" selectable={!readOnly}
        emptyContent={t("previewEmpty")} bulkActions={!readOnly && onChange ? (ids, clear) => <>
          <SelectionBarAction disabled={pending} onClick={() => { onChange(applyInventorySelection(selection, selectionRows.filter((row) => ids.has(row.id)), "include")); clear(); }}>{t("includeChecked")}</SelectionBarAction>
          <SelectionBarAction disabled={pending} onClick={() => { onChange(applyInventorySelection(selection, selectionRows.filter((row) => ids.has(row.id)), "exclude")); clear(); }}>{t("excludeChecked")}</SelectionBarAction>
        </> : undefined} />
    </> : <InlineEmpty label={t("previewEmpty")} />}
  </div>;
}
