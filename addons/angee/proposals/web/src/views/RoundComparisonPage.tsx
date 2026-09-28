import { EmptyState, ErrorBanner, LoadingPanel, Page, PageBody, PageHeader, TextLink, useRouteHref, useRouteParam } from "@angee/ui";
import * as React from "react";
import { useRoundComparisonData } from "../comparison-data";
import { RoundComparisonGrid } from "../comparison-grid";
import { useProposalsT } from "../i18n";
import { RoundCloseControls } from "../round-actions";

/** Headline proposal tabulation route; visibility is exactly the server result. */
export function RoundComparisonPage(): React.ReactElement {
  const id = useRouteParam("id") ?? "";
  const t = useProposalsT();
  const routeHref = useRouteHref();
  const data = useRoundComparisonData(id);
  const roundName = String(data.round?.name ?? id);

  return (
    <Page>
      <PageHeader
        title={t("comparison.title", { round: roundName })}
        description={t("comparison.description")}
        crumbs={
          <TextLink href={routeHref("proposals.rounds.record", { id })}>
            {t("comparison.back")}
          </TextLink>
        }
        actions={
          data.round ? (
            <RoundCloseControls roundId={id} round={data.round} />
          ) : null
        }
      />
      <PageBody gutter="none">
        {data.error ? (
          <div className="p-5">
            <ErrorBanner
              title={t("comparison.error")}
              description={data.error.message}
            />
          </div>
        ) : data.fetching && !data.round ? (
          <LoadingPanel message={t("comparison.loading")} />
        ) : !data.fetching && data.proposals.length === 0 ? (
          <EmptyState
            fill
            icon="proposals-round"
            title={t("comparison.empty.title")}
            description={t("comparison.empty.description")}
          />
        ) : (
          <RoundComparisonGrid
            topics={data.topics}
            proposals={data.proposals}
            answers={data.answers}
            proposalHref={(proposalId) =>
              routeHref("proposals.proposals.record", { id: proposalId })
            }
          />
        )}
      </PageBody>
    </Page>
  );
}
