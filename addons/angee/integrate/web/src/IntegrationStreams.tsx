import type { ActionFieldName } from "@angee/gql/console/actions";
import type { Row } from "@angee/metadata";
import {
  Button,
  Code,
  ListView,
  TextLink,
  defineRowAction,
  jsonObjectFromUnknown,
  optionToken,
  useActionResultMutation,
  useEnumOptions,
  useRecordChromeContext,
  useResourceRecordHrefLookup,
  type ListColumn,
  type RowActionDeclaration,
  type StringIdRow,
  type WidgetDefinition,
  type WidgetRenderProps,
} from "@angee/ui";
import { useMemo, useState, type ReactElement } from "react";

import { useIntegrateT } from "./i18n";
import { IntegrationSyncRunLink } from "./sync-fragments";

export const INTEGRATION_STREAMS_TAB_ID = "integrate.streams";
export const SYNC_STREAM_MODEL = "integrate.SyncStream";
export const SYNC_DISCREPANCY_MODEL = "integrate.SyncDiscrepancy";
export const RECORD_LINK_MODEL = "integrate.RecordLink";

interface StreamRow extends StringIdRow {
  key?: string;
  partition?: string;
  kind?: string;
  resync_required?: boolean;
}

interface DiscrepancyRow extends StringIdRow {
  status?: string;
}

interface LinkRow extends StringIdRow {
  model_label?: string;
  record_id?: string;
}

type StreamSelection =
  | { view: "streams" }
  | { view: "discrepancies" | "links"; stream: StreamRow };

/** The saved-record tab reads its declared, backend-annotated presence field. */
export function integrationHasStreams(record: Row): boolean {
  return typeof record.stream_count === "number" && record.stream_count > 0;
}

export function StreamsLabel(): ReactElement {
  const t = useIntegrateT();
  return <>{t("streams.title")}</>;
}

/** A record change resets the drill-down while the form owns the parent identity. */
export function IntegrationStreamsPane(): ReactElement {
  const { recordId, record, dataProviderName } = useRecordChromeContext();
  return (
    <IntegrationStreams
      key={recordId}
      integrationId={recordId}
      progress={record?.sync_progress}
      dataProviderName={dataProviderName}
    />
  );
}

function IntegrationStreams({ integrationId, progress, dataProviderName }: {
  integrationId: string;
  progress: unknown;
  dataProviderName: string | undefined;
}): ReactElement {
  const t = useIntegrateT();
  const recordHref = useResourceRecordHrefLookup();
  const discrepancyStatuses = useEnumOptions(SYNC_DISCREPANCY_MODEL, "status");
  const openStatuses = discrepancyStatuses
    .filter((option) => ["open", "retry"].includes(optionToken(option.value)))
    .map((option) => option.value);
  const [selection, setSelection] = useState<StreamSelection>({ view: "streams" });
  const [resync] = useActionResultMutation<ActionFieldName>("resyncSyncStream", {
    dataProviderName,
    invalidateModels: [SYNC_STREAM_MODEL],
  });
  const [resolve] = useActionResultMutation<ActionFieldName>("resolveSyncDiscrepancy", {
    dataProviderName,
    invalidateModels: [SYNC_DISCREPANCY_MODEL, SYNC_STREAM_MODEL],
  });
  const [retry] = useActionResultMutation<ActionFieldName>("retrySyncDiscrepancy", {
    dataProviderName,
    invalidateModels: [SYNC_DISCREPANCY_MODEL, SYNC_STREAM_MODEL],
  });
  const streamColumns = useMemo<readonly ListColumn<StreamRow>[]>(() => [
    { field: "key" },
    { field: "partition" },
    { field: "kind" },
    { field: "direction" },
    { field: "generation" },
    { field: "phase" },
    { field: "cursor", widget: "angee.integrate.sync_cursor", sortable: false },
    { field: "last_advanced_at" },
    { field: "last_reconciled_at" },
    { field: "open_discrepancy_count", header: t("streams.openDiscrepancies") },
    { field: "link_count", header: t("streams.links") },
    { field: "resync_required" },
  ], [t]);
  const streamActions = useMemo<readonly RowActionDeclaration<StreamRow>[]>(() => [
    defineRowAction<StreamRow>({
      kind: "page",
      id: "resync",
      label: t("streams.resync"),
      variant: "ghost",
      pendingPolicy: "active-row",
      disabled: (row) => row.resync_required === true,
      confirm: {
        title: () => t("streams.resyncConfirm.title"),
        body: () => t("streams.resyncConfirm.body"),
        confirm: () => t("streams.resync"),
      },
      onSelect: (row) => resync(row.id),
    }),
    defineRowAction<StreamRow>({
      kind: "page",
      id: "open-discrepancies",
      label: t("streams.openDiscrepancies"),
      variant: "ghost",
      pendingPolicy: "disable-actions",
      onSelect: (stream) => setSelection({ view: "discrepancies", stream }),
    }),
    defineRowAction<StreamRow>({
      kind: "page",
      id: "open-links",
      label: t("streams.openLinks"),
      variant: "ghost",
      pendingPolicy: "disable-actions",
      visible: (row) => optionToken(row.kind) === "record_replica",
      onSelect: (stream) => setSelection({ view: "links", stream }),
    }),
  ], [resync, t]);
  const discrepancyColumns = useMemo<readonly ListColumn<DiscrepancyRow>[]>(() => [
    { field: "kind" },
    { field: "code" },
    { field: "status" },
    { field: "attempts" },
    { field: "retry_at" },
    { field: "created_at" },
  ], []);
  const discrepancyActions = useMemo<readonly RowActionDeclaration<DiscrepancyRow>[]>(() => [
    defineRowAction<DiscrepancyRow>({
      kind: "page",
      id: "resolve",
      label: t("streams.resolve"),
      variant: "ghost",
      pendingPolicy: "active-row",
      visible: (row) => optionToken(row.status) !== "resolved",
      onSelect: (row) => resolve(row.id),
    }),
    defineRowAction<DiscrepancyRow>({
      kind: "page",
      id: "retry",
      label: t("streams.retry"),
      variant: "ghost",
      pendingPolicy: "active-row",
      visible: (row) => optionToken(row.status) !== "resolved",
      onSelect: (row) => retry(row.id),
    }),
  ], [resolve, retry, t]);
  const linkColumns = useMemo<readonly ListColumn<LinkRow>[]>(() => [
    { field: "external_key" },
    { field: "status" },
    { field: "origin" },
    { field: "remote_version" },
    { field: "last_seen_at" },
    {
      field: "record_id",
      header: t("streams.target"),
      sortable: false,
      interactive: true,
      render: (row) => {
        const href = row.model_label && row.record_id
          ? recordHref(row.model_label, row.record_id)
          : undefined;
        return href
          ? <TextLink href={href}>{t("streams.openTarget")}</TextLink>
          : t("streams.targetUnavailable");
      },
    },
  ], [recordHref, t]);
  const runLink = <IntegrationSyncRunLink value={progress} />;
  if (selection.view === "streams") {
    return (
      <ListView<StreamRow>
        resource={SYNC_STREAM_MODEL}
        presentation="embedded"
        scope="local"
        baseFilter={{ integration: { exact: integrationId } }}
        defaultGroup={{ field: "key" }}
        order={{ key: "ASC", partition: "ASC", generation: "DESC" }}
        columns={streamColumns}
        rowActions={streamActions}
        toolbarActions={runLink}
      />
    );
  }
  const toolbar = <>
    <Button size="sm" variant="ghost" onClick={() => setSelection({ view: "streams" })}>
      {t("streams.back")}
    </Button>
    <span className="text-13 text-fg-muted">
      {t(selection.view === "discrepancies" ? "streams.discrepancyScope" : "streams.linkScope", {
        key: selection.stream.key ?? "",
        partition: selection.stream.partition ?? "",
      })}
    </span>
    {runLink}
  </>;
  return selection.view === "discrepancies" ? (
    <ListView<DiscrepancyRow>
      key={`discrepancies:${selection.stream.id}`}
      resource={SYNC_DISCREPANCY_MODEL}
      presentation="embedded"
      scope="local"
      baseFilter={{ stream: { exact: selection.stream.id }, status: { inList: openStatuses } }}
      order={{ created_at: "DESC" }}
      columns={discrepancyColumns}
      rowActions={discrepancyActions}
      toolbarActions={toolbar}
    />
  ) : (
    <ListView<LinkRow>
      key={`links:${selection.stream.id}`}
      resource={RECORD_LINK_MODEL}
      presentation="embedded"
      scope="local"
      baseFilter={{ stream: { exact: selection.stream.id } }}
      order={{ external_key: "ASC" }}
      fields={["model_label"]}
      columns={linkColumns}
      toolbarActions={toolbar}
    />
  );
}

/** Compact top-level cursor evidence; nested payloads never enter the cell. */
function SyncCursorSummary({ value }: WidgetRenderProps): ReactElement {
  const t = useIntegrateT();
  const compact = (text: string, limit = 48): string => text.length > limit
    ? `${text.slice(0, limit - 1)}…`
    : text;
  const summarize = (item: unknown): string => {
    if (Array.isArray(item)) return t("streams.cursor.items", { count: item.length });
    if (item !== null && typeof item === "object") return t("streams.cursor.object");
    if (item == null) return t("streams.cursor.empty");
    return compact(String(item));
  };
  const object = jsonObjectFromUnknown(value);
  const entries = object ? Object.entries(object) : [];
  const summary = object
    ? entries.slice(0, 4).map(([key, item]) => `${compact(key, 24)}: ${summarize(item)}`).join(" · ")
    : summarize(value);
  return <Code truncate>{summary || t("streams.cursor.empty")}{entries.length > 4 ? " …" : ""}</Code>;
}

export const integrationSyncCursorWidget = {
  read: SyncCursorSummary,
} satisfies WidgetDefinition;
