import type { DocumentType } from "@angee/gql/console";
import type { ActionFieldName } from "@angee/gql/console/actions";
import type { Row } from "@angee/metadata";
import { RecordHistoryTimeline } from "@angee/provenance";
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
import { IntegrationSyncStream } from "./documents";

export const INTEGRATION_STREAMS_TAB_ID = "integrate.streams";
export const SYNC_STREAM_MODEL = "integrate.SyncStream";
export const RECORD_LINK_MODEL = "integrate.RecordLink";

interface StreamRow extends StringIdRow {
  key?: string;
  partition?: string;
  kind?: string;
  resync_required?: boolean;
}

type FailureRow = NonNullable<NonNullable<DocumentType<
  typeof IntegrationSyncStream
>["sync_streams_by_pk"]>["failures"]>[number];

interface LinkRow extends StringIdRow {
  model_label?: string;
  record_id?: string;
  pinned?: boolean;
}

export const INTEGRATION_STREAM_SEARCH_KEYS = {
  integration: "syncIntegration",
  stream: "syncStream",
  view: "syncView",
  link: "syncLink",
} as const;

type StreamView = "streams" | "failures" | "links" | "history";

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
  const view: StreamView = streamId && (requestedView === "failures" || requestedView === "links" || (requestedView === "history" && linkId))
    ? requestedView : "streams";
  const streamRead = useAuthoredQuery(IntegrationSyncStream, { id: streamId ?? "", includeFailures: view === "failures" }, {
    dataProviderName,
    enabled: view !== "streams",
    models: [SYNC_STREAM_MODEL, RECORD_LINK_MODEL],
  });
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
    idArgument: "stream",
    dataProviderName,
    invalidateModels: [SYNC_STREAM_MODEL, RECORD_LINK_MODEL],
  });
  const [retry] = useActionResultMutation<ActionFieldName>("retrySyncFailure", {
    idArgument: "stream",
    dataProviderName,
    invalidateModels: [SYNC_STREAM_MODEL, RECORD_LINK_MODEL],
  });
  const [pin] = useActionResultMutation<ActionFieldName>("pinRecordLink", {
    dataProviderName, invalidateModels: [RECORD_LINK_MODEL],
  });
  const [unpin] = useActionResultMutation<ActionFieldName>("unpinRecordLink", {
    dataProviderName, invalidateModels: [RECORD_LINK_MODEL],
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
    { field: "open_failure_count", header: t("streams.openFailures") },
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
      id: "open-failures",
      label: t("streams.openFailures"),
      variant: "ghost",
      pendingPolicy: "disable-actions",
      onSelect: (stream) => show("failures", stream.id),
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
  const failureColumns = useMemo<readonly ListColumn<FailureRow>[]>(() => [
    { field: "kind" },
    { field: "code" },
    { field: "status" },
    { field: "attempts" },
    { field: "retry_at" },
    { field: "created_at" },
  ], []);
  const failureActions = useMemo<readonly RowActionDeclaration<FailureRow>[]>(() => [
    defineRowAction<FailureRow>({
      kind: "page",
      id: "resolve",
      label: t("streams.resolve"),
      variant: "ghost",
      pendingPolicy: "active-row",
      visible: (row) => row.is_open === true && row.kind !== "CONFLICT",
      onSelect: (row) => resolve(row.stream_id, { link: row.link_id, slot: row.slot }),
    }),
    ...(["REMOTE", "LOCAL"] as const).map((keep) => defineRowAction<FailureRow>({
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
      onSelect: (row) => resolve(row.stream_id, { link: row.link_id, slot: row.slot, keep }),
    })),
    defineRowAction<FailureRow>({
      kind: "page",
      id: "retry",
      label: t("streams.retry"),
      variant: "ghost",
      pendingPolicy: "active-row",
      visible: (row) => row.is_open === true,
      onSelect: (row) => retry(row.stream_id, { link: row.link_id, slot: row.slot }),
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
  const linkActions = useMemo<readonly RowActionDeclaration<LinkRow>[]>(() => [
    defineRowAction<LinkRow>({
      kind: "page", id: "pin", label: t("streams.pin"), variant: "ghost",
      pendingPolicy: "active-row", visible: (row) => row.pinned !== true,
      onSelect: (row) => pin(row.id),
    }),
    defineRowAction<LinkRow>({
      kind: "page", id: "unpin", label: t("streams.unpin"), variant: "ghost",
      pendingPolicy: "active-row", visible: (row) => row.pinned === true,
      onSelect: (row) => unpin(row.id),
    }),
    defineRowAction<LinkRow>({
      kind: "page", id: "history", label: t("streams.history"), variant: "ghost",
      pendingPolicy: "disable-actions", onSelect: (row) => show("history", streamId, row.id),
    }),
  ], [pin, show, streamId, t, unpin]);
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
      {t(view === "failures" ? "streams.failureScope" : "streams.linkScope", {
        key: stream.key, partition: stream.partition,
      })}
    </span> : null}
  </>;
  return view === "failures" ? (
    <RowsListView<FailureRow>
      key={`failures:${streamId}`}
      rows={stream?.failures ?? []}
      fetching={streamRead.isFetching}
      error={streamRead.error}
      presentation="embedded"
      scope="local"
      columns={failureColumns}
      rowActions={failureActions}
      toolbarActions={toolbar}
    />
  ) : view === "history" ? (
    // A link's observations are its record history: the provenance timeline shows
    // each sync's payload, hash and application facts as field deltas per changeset.
    <div key={`history:${linkId}`} className="grid min-w-0 gap-3">
      <div className="flex flex-wrap items-center gap-2">{toolbar}</div>
      <RecordHistoryTimeline model={RECORD_LINK_MODEL} id={linkId} dataProviderName={dataProviderName} />
    </div>
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
