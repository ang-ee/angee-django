import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import { DashboardView, ErrorBanner, Metric, ContainerOutlet, useContainer } from "@angee/ui";

import { PartiesOverview } from "./documents";
import { usePartiesT } from "./i18n";

/** Relationship-management overview owned by Parties and extended through its slot. */
export function OverviewPage(): React.ReactElement {
  const t = usePartiesT();
  const overview = useAuthoredQuery(PartiesOverview, undefined, {
    models: [
      "parties.Party",
      "parties.Person",
      "parties.Organization",
      "parties.Handle",
      "parties.PartyStatement",
      "parties.PartySameAsPairStatement",
    ],
  });
  const contributions = useContainer("parties.overview#items");
  const data = overview.data;

  return (
    <DashboardView>
      <Metric
        label={t("overview.metric.contacts")}
        value={data?.contacts.aggregate?.count}
        format="count"
        loading={overview.isFetching}
        icon="parties"
      />
      <Metric
        label={t("overview.metric.organizations")}
        value={data?.organizations.aggregate?.count}
        format="count"
        loading={overview.isFetching}
        icon="organization"
        tone="brand"
      />
      <Metric
        label={t("overview.metric.unresolvedHandles")}
        value={data?.unresolved_handles.aggregate?.count}
        format="count"
        loading={overview.isFetching}
        icon="handle"
        tone="warning"
      />
      <Metric
        label={t("overview.metric.reviewQueue")}
        value={data?.review_queue.aggregate?.count}
        format="count"
        loading={overview.isFetching}
        icon="user-check"
        tone="warning"
      />
      <Metric
        label={t("overview.metric.sameAs")}
        value={data?.same_as_pair_counts.pairs}
        format="count"
        loading={overview.isFetching}
        icon="users"
        tone="warning"
      />

      {overview.error ? <ErrorBanner description={t("overview.error")} /> : null}
      <ContainerOutlet entries={contributions} />
    </DashboardView>
  );
}
