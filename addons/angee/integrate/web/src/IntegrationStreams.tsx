import type { ActionFieldName } from "@angee/gql/console/actions";
import type { Row } from "@angee/metadata";
import { extractActionOutcome, useAuthoredMutation } from "@angee/refine";
import {
  Button,
  Code,
  ListView,
  TextLink,
  defineRowAction,
  jsonObjectFromUnknown,
  optionToken,
  useActionResultMutation,
  useActionResultRun,
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
import { ResolveSyncDiscrepancy } from "./documents";

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
  kind?: string;
  is_open?: boolean;
}

interface LinkRow extends StringIdRow {
  model_label?: string;
  record_id?: string;
}

export const INTEGRATION_STREAM_SEARCH_KEYS = {
  integration: "syncIntegration",
  stream: "syncStream",
  view: "syncView",
} as const;

type StreamView = "streams" | "discrepancies" | "links";

/** The saved-record tab reads its declared, backend-annotated presence field. */
export function integrationHasStreams(record: Row): boolean {
  return typeof record.stream_count === "number" && record.stream_count > 0;
}

export function StreamsLabel(): ReactElement {
  const t = useIntegrateT();
  return <>{t("streams.title")}</>;
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
  const requestedView = routeSearchParam(search, INTEGRATION_STREAM_SEARCH_KEYS.view);
  const view: StreamView = streamId && (requestedView === "discrepancies" || requestedView === "links")
    ? requestedView : "streams";
  const show = useCallback((nextView: StreamView, id?: string): void => {
    void navigate({ to: ".", search: updateRouteSearch({
      [INTEGRATION_STREAM_SEARCH_KEYS.integration]: id ? integrationId : undefined,
      [INTEGRATION_STREAM_SEARCH_KEYS.stream]: id,
      [INTEGRATION_STREAM_SEARCH_KEYS.view]: id ? nextView : undefined,
    }) });
  }, [integrationId, navigate]);
  const [resync] = useActionResultMutation<ActionFieldName>("resyncSyncStream", {
    dataProviderName,
    invalidateModels: [SYNC_STREAM_MODEL],
  });
  const [resolve] = useAuthoredMutation(ResolveSyncDiscrepancy, {
    dataProviderName,
    invalidateModels: [SYNC_DISCREPANCY_MODEL, SYNC_STREAM_MODEL, RECORD_LINK_MODEL],
  });
  const settle = useActionResultRun();
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
      visible: (row) => optionToken(row.kind) === "record_replica",
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
      visible: (row) => row.is_open === true && optionToken(row.kind) !== "conflict",
      onSelect: async (row) => { await settle(async () => extractActionOutcome(
        await resolve({ id: row.id }), "resolveSyncDiscrepancy",
      )); },
    }),
    ...(["remote", "local"] as const).map((keep) => defineRowAction<DiscrepancyRow>({
      kind: "page",
      id: `keep-${keep}`,
      label: t(keep === "remote" ? "streams.keepRemote" : "streams.keepLocal"),
      variant: "ghost",
      pendingPolicy: "active-row",
      visible: (row) => row.is_open === true && optionToken(row.kind) === "conflict",
      onSelect: async (row) => { await settle(async () => extractActionOutcome(
        await resolve({ id: row.id, keep }), "resolveSyncDiscrepancy",
      )); },
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
  ], [resolve, retry, settle, t]);
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
    <span className="text-13 text-fg-muted">
      {t(view === "discrepancies" ? "streams.discrepancyScope" : "streams.linkScope", { stream: streamId })}
    </span>
  </>;
  return view === "discrepancies" ? (
    <ListView<DiscrepancyRow>
      key={`discrepancies:${streamId}`}
      resource={SYNC_DISCREPANCY_MODEL}
      presentation="embedded"
      scope="local"
      baseFilter={{ stream: { exact: streamId }, is_open: { exact: true } }}
      order={{ created_at: "DESC" }}
      fields={["is_open"]}
      columns={discrepancyColumns}
      rowActions={discrepancyActions}
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
