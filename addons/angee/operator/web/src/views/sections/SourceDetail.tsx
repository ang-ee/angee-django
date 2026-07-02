import { Code, DetailSection, DetailSurface, useRouteRecordId } from "@angee/ui";
import { type ReactElement } from "react";

import { useOperatorT } from "../../i18n";
import { useOperatorSnapshot } from "../../data/transport";
import { RowActions } from "../parts/RowActions";
import { StateTag } from "../parts/StateTag";
import { useSourceActions } from "./source-actions";

/** Source detail: drift readout + the source's git actions (no log stream). */
export function SourceDetail(): ReactElement {
  const t = useOperatorT();
  const name = useRouteRecordId();
  const { snapshot, result, refetch } = useOperatorSnapshot({ sources: true });
  const { actions, busy } = useSourceActions(refetch);

  const source = (snapshot?.sources ?? []).find((candidate) => candidate.name === name) ?? null;

  return (
    <DetailSurface
      loading={result.fetching && !snapshot}
      loadingMessage={t("sources.loading")}
      empty={
        !source
          ? {
              icon: "share",
              title: t("sources.detail.notFound"),
              description: name,
            }
          : null
      }
      title={source?.name}
      meta={
        source ? (
          <>
            <StateTag state={source.state ?? "unknown"} />
            <span className="text-fg-muted">{source.kind}</span>
          </>
        ) : null
      }
      actions={
        source ? (
          <RowActions
            actions={actions}
            busy={busy}
            subject={source}
            className="flex flex-wrap gap-1"
          />
        ) : undefined
      }
    >
      {source ? (
        <DetailSection
          title={t("sources.detail.overview")}
          rows={[
            [t("sources.column.kind"), source.kind],
            [
              t("sources.column.status"),
              <StateTag state={source.state ?? "unknown"} />,
            ],
            [t("sources.column.branch"), source.branch ?? "—"],
            [
              t("sources.column.aheadBehind"),
              <span className="tabular-nums">
                ↑{source.ahead ?? 0} ↓{source.behind ?? 0}
              </span>,
            ],
            [
              t("sources.column.dirty"),
              source.dirty
                ? t("sources.dirty")
                : t("sources.clean"),
            ],
            [t("sources.detail.upstream"), source.upstream ?? "—"],
            [
              t("sources.detail.currentRef"),
              source.currentRef ? (
                <Code truncate>{source.currentRef}</Code>
              ) : (
                "—"
              ),
            ],
            [
              t("sources.detail.pushed"),
              source.pushed
                ? t("gitops.pushed.yes")
                : t("gitops.pushed.no"),
            ],
            [
              t("sources.detail.path"),
              <Code truncate>{source.path}</Code>,
            ],
            ...(source.error
              ? ([
                  [
                    t("sources.detail.error"),
                    <span className="text-danger-text">{source.error}</span>,
                  ],
                ] as const)
              : []),
          ]}
        />
      ) : null}
    </DetailSurface>
  );
}
