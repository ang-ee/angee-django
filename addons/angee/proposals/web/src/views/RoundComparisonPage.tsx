import { NavLink, Page, PageBody, PageHeader, useRouteHref, useRouteParam } from "@angee/ui";
import * as React from "react";
import { useRoundComparisonData } from "../comparison-data";
import { RoundComparisonBody } from "../comparison-body";
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
          <NavLink href={routeHref("proposals.rounds.record", { id })} variant="inline">
            {t("comparison.back")}
          </NavLink>
        }
        actions={
          data.round ? (
            <RoundCloseControls roundId={id} round={data.round} />
          ) : null
        }
      />
      <PageBody gutter="none">
        <RoundComparisonBody data={data} proposalHref={(proposalId) =>
          routeHref("proposals.proposals.record", { id: proposalId })} />
      </PageBody>
    </Page>
  );
}
