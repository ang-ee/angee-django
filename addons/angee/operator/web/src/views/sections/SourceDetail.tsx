import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Code,
  EmptyState,
  LoadingPanel,
  MetaGrid,
  RecordHeader,
} from "@angee/base";
import { type ReactElement } from "react";
import { useParams } from "@tanstack/react-router";

import { useOperatorT } from "../../i18n";
import { useOperatorSnapshot } from "../../data/transport";
import { StateTag } from "../parts/StateTag";
import { SourceActions, useSourceActions } from "./source-actions";

/** Source detail: drift readout + the source's git actions (no log stream). */
export function SourceDetail(): ReactElement {
  const t = useOperatorT();
  const params = useParams({ strict: false });
  const name = "name" in params && typeof params.name === "string" ? params.name : undefined;
  const { snapshot, result, refetch } = useOperatorSnapshot({ sources: true });
  const { actions, busy } = useSourceActions(refetch);

  const source = (snapshot?.sources ?? []).find((candidate) => candidate.name === name) ?? null;

  if (result.fetching && !snapshot) {
    return <LoadingPanel message={t("operator.sources.loading")} />;
  }
  if (!source) {
    return (
      <EmptyState
        fill
        icon="share"
        title={t("operator.sources.detail.notFound")}
        description={name}
      />
    );
  }

  return (
    <div className="flex min-h-0 flex-col gap-4 p-4">
      <RecordHeader
        title={source.name}
        meta={
          <>
            <StateTag state={source.state ?? "unknown"} />
            <span className="text-fg-muted">{source.kind}</span>
          </>
        }
      />

      <SourceActions
        actions={actions}
        busy={busy}
        source={source}
        className="flex flex-wrap gap-1"
      />

      <Card>
        <CardHeader>
          <CardTitle>{t("operator.sources.detail.overview")}</CardTitle>
        </CardHeader>
        <CardContent>
          <MetaGrid
            rows={[
              [t("operator.sources.column.kind"), source.kind],
              [t("operator.sources.column.status"), <StateTag state={source.state ?? "unknown"} />],
              [t("operator.sources.column.branch"), source.branch ?? "—"],
              [
                t("operator.sources.column.aheadBehind"),
                <span className="tabular-nums">↑{source.ahead ?? 0} ↓{source.behind ?? 0}</span>,
              ],
              [
                t("operator.sources.column.dirty"),
                source.dirty ? t("operator.sources.dirty") : t("operator.sources.clean"),
              ],
              [t("operator.sources.detail.upstream"), source.upstream ?? "—"],
              [
                t("operator.sources.detail.currentRef"),
                source.currentRef ? <Code truncate>{source.currentRef}</Code> : "—",
              ],
              [
                t("operator.sources.detail.pushed"),
                source.pushed ? t("operator.gitops.pushed.yes") : t("operator.gitops.pushed.no"),
              ],
              [t("operator.sources.detail.path"), <Code truncate>{source.path}</Code>],
              ...(source.error
                ? ([[
                    t("operator.sources.detail.error"),
                    <span className="text-danger-text">{source.error}</span>,
                  ]] as const)
                : []),
            ]}
          />
        </CardContent>
      </Card>
    </div>
  );
}
