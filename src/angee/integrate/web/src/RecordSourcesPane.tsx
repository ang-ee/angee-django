import type { StatementReferenceProps } from "@angee/provenance";
import { useAuthoredQuery } from "@angee/refine";
import {
  ErrorBanner,
  RecordReference,
  NavLink,
  useResourceRecordHrefLookup,
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

/** The Sources group of the Links tab: the record's retained provider identities. */
export const recordSourcesGroup: ContainerChild<RelatedGroupContent> = {
  sequence: 70,
  content: {
    label: enIntegrateMessages["sources.title"]!,
    edge: { model: RECORD_LINK_MODEL },
    useCount: (context) => {
      const query = useRecordSources(context);
      return query.data ? query.data.record_links.length : undefined;
    },
    render: (context, label) => <>{label()}<li><RecordSourcesPane context={context} /></li></>,
  },
};

/** One row per source: the integration, its stream, the status and the external key; connectors own their outbound URLs. */
export function RecordSourcesPane({ context }: { context: ChatterViewContext }): React.ReactElement {
  const t = useIntegrateT();
  const query = useRecordSources(context);
  const rows = query.data?.record_links ?? [];
  const recordHref = useResourceRecordHrefLookup();

  if (query.error) return <ErrorBanner description={query.error.message} />;
  if (query.isLoading) return <LoadingPanel message={t("sources.loading")} density="inline" />;
  if (rows.length === 0) return <InlineEmpty icon="link" label={t("sources.empty")} />;

  return <PaneRows>
    {rows.map((source) => {
      const target = source.stream.integration.concrete_target;
      const connectionHref = target.resource && target.id ? recordHref(target.resource, target.id) : undefined;
      return <li key={source.id}>
      <PaneRow
        icon="link"
        title={source.source_label}
        detail={source.stream.integration.display_name}
        meta={<StatusBadge value={source.status} display="pill" />}
        sub={source.last_seen_at ? formatDateTime(source.last_seen_at) : t("sources.never")}
        verb={connectionHref ? <NavLink href={connectionHref} variant="inline">{t("sources.connection")}</NavLink> : undefined}
        href={source.source_url ?? undefined}
        target="_blank" rel="noopener"
      />
    </li>; })}
  </PaneRows>;
}


/** Integrate owns navigation from retained source evidence to the record's Sources tab. */
export function StatementRecordLinkReference({ reference, record }: StatementReferenceProps): React.ReactElement {
  return <RecordReference model={record.model} id={record.id} label={reference.label}
    search={{ chatterTab: "integrate.sources", source: reference.id }} />;
}
