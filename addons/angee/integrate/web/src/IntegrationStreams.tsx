import type { DocumentType } from "@angee/gql/console";
import type { ActionFieldName } from "@angee/gql/console/actions";
import type { Row } from "@angee/metadata";
import { useAuthoredQuery } from "@angee/refine";
import {
  Button,
  Code,
  ListView,
  RowsListView,
  NavLink,
  defineRowAction,
  jsonObjectFromUnknown,
  useActionResultMutation,
  routeSearchParam,
  updateRouteSearch,
  useRouteSearch,
  useRecordChromeContext,
  useResourceRecordHrefLookup,
  type ListColumn,
  type RowActionDeclaration,
  type StringIdRow,
  type WidgetDefinition,
  type WidgetRenderProps,
} from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";
import { useCallback, useMemo, type ReactElement } from "react";

import { useIntegrateT } from "./i18n";
import { IntegrationRecordLinkHistory, IntegrationSyncStream } from "./documents";

export const INTEGRATION_STREAMS_TAB_ID = "integrate.streams";
export const SYNC_STREAM_MODEL = "integrate.SyncStream";
export const RECORD_LINK_MODEL = "integrate.RecordLink";

interface StreamRow extends StringIdRow {
  key?: string;
  partition?: string;
  kind?: string;
  resync_required?: boolean;
}

type DiscrepancyRow = NonNullable<NonNullable<DocumentType<
  typeof IntegrationSyncStream
>["sync_streams_by_pk"]>["failures"]>[number];
type HistoryRow = NonNullable<DocumentType<
  typeof IntegrationRecordLinkHistory
>["record_links_by_pk"]>["history"][number] & StringIdRow;

interface LinkRow extends StringIdRow {
  model_label?: string;
  record_id?: string;
}

export const INTEGRATION_STREAM_SEARCH_KEYS = {
  integration: "syncIntegration",
  stream: "syncStream",
  view: "syncView",
  link: "syncLink",
} as const;

type StreamView = "streams" | "discrepancies" | "links" | "history";

/** The saved-record tab reads its declared, backend-annotated presence field. */
export function integrationHasStreams(record: Row): boolean {
  return typeof record.stream_count === "number" && record.stream_count > 0;
}


/** The parent record owns scope; route search owns the portable drill-down. */
export function IntegrationStreamsPane(): ReactElement {
  const { recordId: integrationId, dataProviderName } = useRecordChromeContext();
  const t = useIntegrateT();
  const recordHref = useResourceRecordHrefLookup();
  const search = useRouteSearch();
  const navigate = useNavigate();
  const streamId = routeSearchParam(search, INTEGRATION_STREAM_SEARCH_KEYS.integration) === integrationId
    ? routeSearchParam(search, INTEGRATION_STREAM_SEARCH_KEYS.stream) : undefined;
  const linkId = routeSearchParam(search, INTEGRATION_STREAM_SEARCH_KEYS.link);
  const requestedView = routeSearchParam(search, INTEGRATION_STREAM_SEARCH_KEYS.view);
  const view: StreamView = streamId && (requestedView === "discrepancies" || requestedView === "links" || (requestedView === "history" && linkId))
    ? requestedView : "streams";
  const streamRead = useAuthoredQuery(IntegrationSyncStream, { id: streamId ?? "", includeFailures: view === "discrepancies" }, {
    dataProviderName,
    enabled: view !== "streams",
    models: [SYNC_STREAM_MODEL, RECORD_LINK_MODEL],
  });
  const historyRead = useAuthoredQuery(IntegrationRecordLinkHistory, { id: linkId ?? "" }, {
    dataProviderName,
    enabled: view === "history",
    models: [RECORD_LINK_MODEL],
  });
  const historyRows = useMemo(() => (historyRead.data?.record_links_by_pk?.history ?? []).map((row) => ({
    ...row, id: String(row.history_id),
  })), [historyRead.data]);
  const stream = streamRead.data?.sync_streams_by_pk;
  const show = useCallback((nextView: StreamView, id?: string, link?: string): void => {
    void navigate({ to: ".", search: updateRouteSearch({
      [INTEGRATION_STREAM_SEARCH_KEYS.integration]: id ? integrationId : undefined,
      [INTEGRATION_STREAM_SEARCH_KEYS.stream]: id,
      [INTEGRATION_STREAM_SEARCH_KEYS.view]: id ? nextView : undefined,
      [INTEGRATION_STREAM_SEARCH_KEYS.link]: link,
    }) });
  }, [integrationId, navigate]);
  const [resync] = useActionResultMutation<ActionFieldName>("resyncSyncStream", {
    dataProviderName,
    invalidateModels: [SYNC_STREAM_MODEL],
  });
  const [resolve] = useActionResultMutation<ActionFieldName>("resolveSyncFailure", {
    dataProviderName,
    invalidateModels: [SYNC_STREAM_MODEL, RECORD_LINK_MODEL],
  });
  const [retry] = useActionResultMutation<ActionFieldName>("retrySyncFailure", {
    dataProviderName,
    invalidateModels: [SYNC_STREAM_MODEL, RECORD_LINK_MODEL],
  });
  const streamColumns = useMemo<readonly ListColumn<StreamRow>[]>(() => [
    { field: "key" },
    { field: "partition" },
    { field: "kind" },
    { field: "direction" },
    { field: "generation" },
    { field: "phase" },
    { field: "state" },
    { field: "cursor", sortable: false },
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
      onSelect: (stream) => show("discrepancies", stream.id),
    }),
    defineRowAction<StreamRow>({
      kind: "page",
      id: "open-links",
      label: t("streams.openLinks"),
      variant: "ghost",
      pendingPolicy: "disable-actions",
      visible: (row) => row.kind === "RECORD_REPLICA",
      onSelect: (stream) => show("links", stream.id),
    }),
  ], [resync, show, t]);
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
      visible: (row) => row.is_open === true && row.kind !== "CONFLICT",
      onSelect: (row) => resolve(row.id),
    }),
    ...(["REMOTE", "LOCAL"] as const).map((keep) => defineRowAction<DiscrepancyRow>({
      kind: "page",
      id: `keep-${keep.toLowerCase()}`,
      label: t(keep === "REMOTE" ? "streams.keepRemote" : "streams.keepLocal"),
      variant: "ghost",
      pendingPolicy: "active-row",
      visible: (row) => row.is_open === true && row.kind === "CONFLICT",
      confirm: {
        title: () => t(keep === "REMOTE" ? "streams.keepRemoteConfirm.title" : "streams.keepLocalConfirm.title"),
        body: () => t(keep === "REMOTE" ? "streams.keepRemoteConfirm.body" : "streams.keepLocalConfirm.body"),
        confirm: () => t(keep === "REMOTE" ? "streams.keepRemote" : "streams.keepLocal"),
      },
      onSelect: (row) => resolve(row.id, { keep }),
    })),
    defineRowAction<DiscrepancyRow>({
      kind: "page",
      id: "retry",
      label: t("streams.retry"),
      variant: "ghost",
      pendingPolicy: "active-row",
      visible: (row) => row.is_open === true,
      onSelect: (row) => retry(row.id),
    }),
  ], [resolve, retry, t]);
  const linkColumns = useMemo<readonly ListColumn<LinkRow>[]>(() => [
    { field: "external_key" },
    { field: "status" },
    { field: "origin" },
    { field: "remote_version" },
    { field: "applied_at" },
    { field: "pinned" },
    { field: "outbox_state" },
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
          ? <NavLink href={href} variant="inline">{t("streams.openTarget")}</NavLink>
          : t("streams.targetUnavailable");
      },
    },
  ], [recordHref, t]);
  const historyColumns = useMemo<readonly ListColumn<HistoryRow>[]>(() => [
    { field: "history_date" },
    { field: "history_type" },
    { field: "source_hash" },
    { field: "remote_version" },
    { field: "mapping_version" },
    { field: "applied_at" },
    { field: "source_payload", sortable: false },
    { field: "mapped_payload", sortable: false },
  ], []);
  const linkActions = useMemo<readonly RowActionDeclaration<LinkRow>[]>(() => [
    defineRowAction<LinkRow>({
      kind: "page", id: "history", label: t("streams.history"), variant: "ghost",
      pendingPolicy: "disable-actions", onSelect: (row) => show("history", streamId, row.id),
    }),
  ], [show, streamId, t]);
  if (view === "streams") {
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
      />
    );
  }
  const toolbar = <>
    <Button size="sm" variant="ghost" onClick={() => show("streams")}>
      {t("streams.back")}
    </Button>
    {stream ? <span className="text-13 text-fg-muted">
      {t(view === "discrepancies" ? "streams.discrepancyScope" : "streams.linkScope", {
        key: stream.key, partition: stream.partition,
      })}
    </span> : null}
  </>;
  return view === "discrepancies" ? (
    <RowsListView<DiscrepancyRow>
      key={`discrepancies:${streamId}`}
      rows={stream?.failures ?? []}
      fetching={streamRead.isFetching}
      error={streamRead.error}
      presentation="embedded"
      scope="local"
      columns={discrepancyColumns}
      rowActions={discrepancyActions}
      toolbarActions={toolbar}
    />
  ) : view === "history" ? (
    <RowsListView<HistoryRow>
      key={`history:${linkId}`}
      rows={historyRows}
      fetching={historyRead.isFetching}
      error={historyRead.error}
      presentation="embedded"
      scope="local"
      columns={historyColumns}
      toolbarActions={toolbar}
    />
  ) : (
    <ListView<LinkRow>
      key={`links:${streamId}`}
      resource={RECORD_LINK_MODEL}
      presentation="embedded"
      scope="local"
      baseFilter={{ stream: { exact: streamId } }}
      order={{ external_key: "ASC" }}
      fields={["model_label"]}
      columns={linkColumns}
      rowActions={linkActions}
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
