import { Code, DetailSection, StatusBadge, useRouteRecordId } from "@angee/ui";
import { type ReactElement } from "react";

import { useOperatorT } from "../../i18n";
import { useOperatorSnapshot } from "../../data/transport";
import { formatDrift } from "../../lib/drift";
import { DaemonRecord } from "../parts/DaemonRecord";
import { useSourceActions } from "./source-actions";

/** Source record page: band verbs and the drift readout (no log stream). */
export function SourceDetail(): ReactElement {
  const t = useOperatorT();
  const name = useRouteRecordId();
  const { snapshot, result, refetch } = useOperatorSnapshot({ sources: true });
  const { actions } = useSourceActions(refetch);
  const source = (snapshot?.sources ?? []).find((candidate) => candidate.name === name) ?? null;

  return (
    <DaemonRecord
      record={source}
      loading={result.fetching && !snapshot}
      loadingMessage={t("sources.loading")}
      notFound={{ icon: "share", title: t("sources.detail.notFound"), description: name }}
      eyebrow={t("sources.record")}
      title={source?.name}
      meta={source ? <><StatusBadge value={source.state ?? "unknown"} /><span>{source.kind}</span></> : null}
      verbs={actions}
    >
      {source ? (
        <DetailSection
          title={t("sources.detail.overview")}
          rows={[
            [t("sources.column.kind"), source.kind],
            [t("sources.column.status"), <StatusBadge value={source.state ?? "unknown"} />],
            [t("sources.column.branch"), source.branch],
            [t("sources.column.aheadBehind"), <span className="tabular-nums">{formatDrift(source.ahead, source.behind)}</span>],
            [t("sources.column.dirty"), <StatusBadge value={source.dirty ? "dirty" : "clean"} />],
            [t("sources.detail.upstream"), source.upstream],
            [t("sources.detail.currentRef"), source.currentRef ? <Code truncate>{source.currentRef}</Code> : null],
            [t("sources.detail.pushed"), <StatusBadge value={source.pushed ? "pushed" : "unpushed"} />],
            [t("sources.detail.path"), <Code truncate>{source.path}</Code>],
            ...(source.error ? ([[t("sources.detail.error"), <Code tone="danger">{source.error}</Code>]] as const) : []),
          ]}
        />
      ) : null}
    </DaemonRecord>
  );
}
