import { CommandBand, Page, PageBody, RecordHeader, useBreadcrumbLeafLabel, useRouteHref, useRouteParam } from "@angee/ui";
import * as React from "react";
import { useRoundComparisonData } from "../comparison-data";
import { RoundComparisonBody } from "../comparison-body";
import { useProposalsT } from "../i18n";
import { RoundCloseControls } from "../round-actions";

/** The round's comparison as a record page: the close verbs on the band, the round in the
 * header, the tabulation as the body. Visibility is exactly the server result. */
export function RoundComparisonPage(): React.ReactElement {
  const id = useRouteParam("id") ?? "";
  const t = useProposalsT();
  const routeHref = useRouteHref();
  const data = useRoundComparisonData(id);
  const roundName = typeof data.round?.name === "string" ? data.round.name : undefined;
  useBreadcrumbLeafLabel(roundName ? t("comparison.title", { round: roundName }) : undefined);

  return (
    <Page>
      <CommandBand verbs={data.round ? <RoundCloseControls roundId={id} round={data.round} /> : null} />
      <RecordHeader density="compact" headingLevel={1} crumbs={t("comparison.eyebrow")}
        title={roundName ?? t("comparison.loading")} description={t("comparison.description")} />
      <PageBody gutter="none">
        <RoundComparisonBody data={data} proposalHref={(proposalId) =>
          routeHref("proposals.proposals.record", { id: proposalId })} />
      </PageBody>
    </Page>
  );
}
