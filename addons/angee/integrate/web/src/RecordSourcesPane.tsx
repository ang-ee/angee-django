import { useAuthoredQuery } from "@angee/refine";
import {
  ErrorBanner,
  InlineEmpty,
  LoadingPanel,
  PaneRow,
  PaneRows,
  StatusBadge,
  formatDateTime,
  type ChatterViewContext,
  type ContainerChild,
  type RelatedGroupContent,
} from "@angee/ui";
import * as React from "react";

import { RecordSourcesDocument } from "./documents";
import { enIntegrateMessages, useIntegrateT } from "./i18n";

export const RECORD_LINK_MODEL = "integrate.RecordLink";
export const RECORD_SOURCES_GROUP_ID = "integrate.sources";

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

function useRecordSources(context: ChatterViewContext) {
  const modelLabel = context.route?.canonicalLabel ?? context.route?.modelLabel ?? "";
  const recordId = context.view.kind === "record" ? context.view.sqid ?? "" : "";
  const variables = React.useMemo(() => ({ modelLabel, recordId }), [modelLabel, recordId]);
  return useAuthoredQuery(RecordSourcesDocument, variables, {
    enabled: Boolean(modelLabel && recordId),
    models: [RECORD_LINK_MODEL],
    records: [{ model: modelLabel, id: recordId }],
  });
}

function sourceRows(data: unknown): readonly RecordSourceRow[] {
  return (data as { record_links?: readonly RecordSourceRow[] } | undefined)?.record_links ?? [];
}

/** The Sources group of the Related tab: the record's retained provider identities. */
export const recordSourcesGroup: ContainerChild<RelatedGroupContent> = {
  sequence: 70,
  content: {
    label: enIntegrateMessages["sources.title"]!,
    edge: { model: RECORD_LINK_MODEL },
    useCount: (context) => {
      const query = useRecordSources(context);
      return query.data ? sourceRows(query.data).length : undefined;
    },
    render: (context, label) => <>{label()}<li><RecordSourcesPane context={context} /></li></>,
  },
};

/** One row per source: the integration, its stream, the status and the external key; connectors own their outbound URLs. */
export function RecordSourcesPane({ context }: { context: ChatterViewContext }): React.ReactElement {
  const t = useIntegrateT();
  const query = useRecordSources(context);
  const rows = sourceRows(query.data);

  if (query.error) return <ErrorBanner description={query.error.message} />;
  if (query.isLoading) return <LoadingPanel message={t("sources.loading")} density="inline" />;
  if (rows.length === 0) return <InlineEmpty icon="link" label={t("sources.empty")} />;

  return <PaneRows>
    {rows.map((source) => <li key={source.id}>
      <PaneRow
        icon="link"
        title={source.stream.integration.display_name}
        detail={`${source.stream.key}${source.stream.partition ? ` · ${source.stream.partition}` : ""}`}
        meta={<StatusBadge value={source.status} display="dot" />}
        sub={`${source.external_key} · ${source.last_seen_at ? formatDateTime(source.last_seen_at) : t("sources.never")}`}
        href={source.source_url ?? undefined}
        target="_blank" rel="noopener"
      />
    </li>)}
  </PaneRows>;
}
