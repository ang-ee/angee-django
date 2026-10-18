import { useAuthoredQuery } from "@angee/refine";
import {
  EmptyState,
  ErrorBanner,
  LoadingPanel,
  NavLink,
  cn,
  formatDateTime,
  routeSearchParam,
  useRouteSearch,
  type ChatterViewContext,
} from "@angee/ui";
import * as React from "react";

import { RecordSourcesDocument } from "./documents";
import { useIntegrateT } from "./i18n";

export const RECORD_LINK_MODEL = "integrate.RecordLink";
export const RECORD_SOURCES_TAB_ID = "integrate.sources";
export const RECORD_SOURCE_SEARCH_KEY = "source";

interface RecordSourceRow {
  id: string;
  external_key: string;
  status: string;
  source_url: string | null;
  last_verified_generation: number;
  last_seen_at: string | null;
  applied_at: string | null;
  stream: {
    id: string;
    key: string;
    partition: string;
    integration: { id: string; display_name: string };
  };
}

/** The record's retained provider identities; connectors own their outbound URLs. */
export function RecordSourcesPane({ context }: { context: ChatterViewContext }): React.ReactElement {
  const t = useIntegrateT();
  const search = useRouteSearch();
  const modelLabel = context.route?.canonicalLabel ?? context.route?.modelLabel ?? "";
  const recordId = context.view.kind === "record" ? context.view.sqid ?? "" : "";
  const variables = React.useMemo(() => ({ modelLabel, recordId }), [modelLabel, recordId]);
  const query = useAuthoredQuery(RecordSourcesDocument, variables, {
    enabled: Boolean(modelLabel && recordId),
    models: [RECORD_LINK_MODEL],
    records: [{ model: modelLabel, id: recordId }],
  });
  const rows = ((query.data as { record_links?: readonly RecordSourceRow[] } | undefined)?.record_links ?? []);
  const selected = routeSearchParam(search, RECORD_SOURCE_SEARCH_KEY);

  if (query.error) return <ErrorBanner description={query.error.message} />;
  if (query.isLoading) return <LoadingPanel message={t("sources.loading")} density="inline" />;
  if (rows.length === 0) return <EmptyState icon="link" title={t("sources.empty")} className="min-h-48 p-4" />;

  return <ul className="min-h-0 overflow-y-auto p-3">
    {rows.map((source) => <li key={source.id} id={`source-${source.id}`} className={cn(
      "rounded-8 border border-border-subtle p-3 [&+&]:mt-2",
      source.id === selected && "border-border-strong bg-inset",
    )}>
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium">{source.stream.integration.display_name}</div>
          <div className="truncate text-xs text-fg-muted">{source.stream.key}{source.stream.partition ? ` · ${source.stream.partition}` : ""}</div>
        </div>
        <span className="shrink-0 text-xs text-fg-muted">{source.status}</span>
      </div>
      <div className="mt-2 min-w-0 text-sm">
        {source.source_url
          ? <NavLink href={source.source_url} target="_blank" variant="inline">{source.external_key}</NavLink>
          : <span className="break-all">{source.external_key}</span>}
      </div>
      <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
        <div><dt className="text-fg-subtle">{t("sources.coverage")}</dt>
          <dd>{source.last_verified_generation > 0
            ? t("sources.generation", { count: source.last_verified_generation })
            : t("sources.notVerified")}</dd></div>
        <div><dt className="text-fg-subtle">{t("sources.lastObserved")}</dt>
          <dd>{source.last_seen_at ? formatDateTime(source.last_seen_at) : t("sources.never")}</dd></div>
        <div><dt className="text-fg-subtle">{t("sources.appliedBase")}</dt>
          <dd>{source.applied_at ? formatDateTime(source.applied_at) : t("sources.notApplied")}</dd></div>
      </dl>
    </li>)}
  </ul>;
}
