import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import {
  DashboardView,
  ErrorBanner,
  Metric,
  SlotOutlet,
  useSlot,
} from "@angee/ui";

import { PartiesOverview } from "./documents";
import { usePartiesT } from "./i18n";
import { PARTIES_OVERVIEW_SLOT } from "./slots";

const DUPLICATE_COUNT_LIMIT = 100;
const DUPLICATE_QUERY_LIMIT = DUPLICATE_COUNT_LIMIT + 1;

/** Relationship-management overview owned by Parties and extended through its slot. */
export function OverviewPage(): React.ReactElement {
  const t = usePartiesT();
  const overview = useAuthoredQuery(PartiesOverview, {
    duplicateLimit: DUPLICATE_QUERY_LIMIT,
  }, {
    models: [
      "parties.Party",
      "parties.Person",
      "parties.Organization",
      "parties.Handle",
      "parties.PartyHandle",
      "parties.MergeVeto",
    ],
  });
  const contributions = useSlot(PARTIES_OVERVIEW_SLOT);
  const data = overview.data;
  const duplicateCount = data?.duplicate_party_candidates.length;

  return (
    <DashboardView className="p-1">
      <Metric
        label={t("overview.metric.contacts")}
        value={metricCount(data?.contacts.aggregate?.count, overview.fetching)}
        icon="parties"
      />
      <Metric
        label={t("overview.metric.organizations")}
        value={metricCount(data?.organizations.aggregate?.count, overview.fetching)}
        icon="organization"
        tone="brand"
      />
      <Metric
        label={t("overview.metric.unresolvedHandles")}
        value={metricCount(data?.unresolved_handles.aggregate?.count, overview.fetching)}
        icon="handle"
        tone="warning"
      />
      <Metric
        label={t("overview.metric.reviewQueue")}
        value={metricCount(data?.review_queue.aggregate?.count, overview.fetching)}
        icon="user-check"
        tone="info"
      />
      <Metric
        label={t("overview.metric.duplicates")}
        value={duplicateMetric(duplicateCount, overview.fetching)}
        icon="users"
        tone="danger"
        detail={duplicateCount !== undefined && duplicateCount > DUPLICATE_COUNT_LIMIT ? t("overview.metric.duplicatesCapped") : undefined}
      />

      {overview.error ? <ErrorBanner description={t("overview.error")} /> : null}
      <SlotOutlet entries={contributions} />
    </DashboardView>
  );
}

function metricCount(value: number | undefined, fetching: boolean): string {
  if (value === undefined && fetching) return "—";
  return (value ?? 0).toLocaleString();
}

function duplicateMetric(value: number | undefined, fetching: boolean): string {
  if (value === undefined && fetching) return "—";
  if (value !== undefined && value > DUPLICATE_COUNT_LIMIT) {
    return `${DUPLICATE_COUNT_LIMIT.toLocaleString()}+`;
  }
  return (value ?? 0).toLocaleString();
}
